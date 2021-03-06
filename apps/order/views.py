from django.shortcuts import redirect, reverse, render
from goods.models import GoodsSKU
from user.models import Address
from django_redis import get_redis_connection
from utils.mixin import LoginRequredMixIn
from django.http import JsonResponse
from order.models import OrderInfo, OrderGoods
from datetime import datetime
from django.db import transaction
from django.conf import settings
from django.views import View
from alipay import AliPay
from time import sleep


# Create your views here.

class OrderPlaceView(LoginRequredMixIn):
    def post(self, request):

        user = request.user

        sku_ids = request.POST.getlist('sku_ids')

        if not sku_ids:
            return redirect(reverse('cart:show'))

        coon = get_redis_connection('default')

        cart_key = 'cart_%d' % user.id

        skus = []
        total_count = 0
        total_price = 0

        for sku_id in sku_ids:
            sku = GoodsSKU.objects.get(pk=sku_id)
            # 获取商品的数量
            conut = coon.hget(cart_key, sku_id)
            amount = sku.price * int(conut)
            sku.count = int(conut.decode())
            sku.amount = amount
            skus.append(sku)
            total_count += int(conut)
            total_price += amount

        # 运费
        transit_price = 10  # 假数据， 写死
        # 实付费
        total_pay = total_price + transit_price

        addrs = Address.objects.filter(user=user)

        sku_ids = ",".join(sku_ids)

        # 组织上下文
        context = {
            'skus': skus,
            'total_count': total_count,
            'total_price': transit_price,
            'transit_price': transit_price,
            'total_pay': total_pay,
            'addrs': addrs,
            'sku_ids': sku_ids}

        return render(request, 'place_order.html', context)


# 悲观锁
class OrderCommitView(LoginRequredMixIn):

    @transaction.atomic
    def post(self, request):
        user = request.user

        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '用户未登录!'})

        addr_id = request.POST.get('addr_id')
        pay_method = request.POST.get('pay_method')
        sku_ids = request.POST.get('sku_ids')

        if not all([addr_id, pay_method, sku_ids]):
            return JsonResponse({'res': 1, 'errmsg': '参数不完整!'})

        # todo 校验支付方式
        if pay_method not in OrderInfo.PAY_METHODS.keys():
            return JsonResponse({'res': 2, 'errmsg': '非法的支付方式!'})

        try:
            addr = Address.objects.get(pk=addr_id)
        except Address.DoesNotExist:
            return JsonResponse({'res': 3, 'errmsg': '地址非法'})

        # TODO: 创建订单
        order_id = datetime.now().strftime('%Y%m%d%H%M%S') + str(user.id)
        # 运费
        transit_price = 10  # 假数据， 写死
        total_count = 0
        total_price = 0
        coon = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id
        # 设置保存点
        save_id = transaction.savepoint()
        try:
            # TODO  想订单信息表中添加一条记录
            order = OrderInfo.objects.create(
                order_id=order_id,
                user=user,
                addr=addr,
                pay_method=pay_method,
                total_count=total_count,
                total_price=total_price,
                transit_price=transit_price
            )

            # todo 用户的订单中有几个商品就要添加几条记录
            sku_ids = sku_ids.split(',')
            for sku_id in sku_ids:
                try:
                    sku = GoodsSKU.objects.select_for_update().get(id=sku_id)  # 悲观锁
                except GoodsSKU.DoesNotExist:
                    transaction.savepoint_rollback(save_id)
                    return JsonResponse({'res': 4, 'errmsg': '商品不存在!'})

                # 获取商品的数目
                count = coon.hget(cart_key, sku_id)

                # todo 判断商品的库存
                if int(count) > sku.stock:
                    transaction.savepoint_rollback(save_id)
                    return JsonResponse({'res': 6, 'errmsg': '商品库存不足'})

                # todo to订单信息表中添加一条记录
                OrderGoods.objects.create(
                    order=order,
                    sku=sku,
                    count=int(count),
                    price=sku.price,
                )

                # todo 更新商品的库存销量和库存
                sku.stock -= int(count)
                sku.sales += int(count)
                sku.save()

                # todo 累加计算商品的订单的总数量和总价格
                amount = sku.price * int(count)
                total_count += int(count)
                total_price += amount

            # todo 更新订单信息表中的商品的总数量和价格
            order.total_count = total_count
            order.total_price = total_price
            order.save()
        except Exception:
            transaction.savepoint_rollback(save_id)
            return JsonResponse({'res': 7, 'errmsg': '下单失败!'})
        # 提交
        transaction.savepoint_commit(save_id)
        # todo 清楚用户的购物车记录
        coon.hdel(cart_key, *sku_ids)

        return JsonResponse({'res': 5, 'message': '创建成功!'})


# 乐观锁
class OrderCommitView2(LoginRequredMixIn):

    @transaction.atomic
    def post(self, request):
        user = request.user

        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '用户未登录!'})

        addr_id = request.POST.get('addr_id')
        pay_method = request.POST.get('pay_method')
        sku_ids = request.POST.get('sku_ids')

        if not all([addr_id, pay_method, sku_ids]):
            return JsonResponse({'res': 1, 'errmsg': '参数不完整!'})

        # todo 校验支付方式
        if pay_method not in OrderInfo.PAY_METHODS.keys():
            return JsonResponse({'res': 2, 'errmsg': '非法的支付方式!'})

        try:
            addr = Address.objects.get(pk=addr_id)
        except Address.DoesNotExist:
            return JsonResponse({'res': 3, 'errmsg': '地址非法'})

        # TODO: 创建订单
        order_id = datetime.now().strftime('%Y%m%d%H%M%S') + str(user.id)
        # 运费
        transit_price = 10  # 假数据， 写死
        total_count = 0
        total_price = 0
        coon = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id
        # 设置保存点
        save_id = transaction.savepoint()
        try:
            # TODO  想订单信息表中添加一条记录
            order = OrderInfo.objects.create(
                order_id=order_id,
                user=user,
                addr=addr,
                pay_method=pay_method,
                total_count=total_count,
                total_price=total_price,
                transit_price=transit_price
            )

            # todo 用户的订单中有几个商品就要添加几条记录
            sku_ids = sku_ids.split(',')
            for sku_id in sku_ids:
                for i in range(settings.NUMBER_OF_FAILED_ORDER_TIMES):
                    try:
                        sku = GoodsSKU.objects.get(id=sku_id)  # 悲观锁
                    except GoodsSKU.DoesNotExist:
                        transaction.savepoint_rollback(save_id)
                        return JsonResponse({'res': 4, 'errmsg': '商品不存在!'})

                    # 获取商品的数目
                    count = coon.hget(cart_key, sku_id)

                    # todo 判断商品的库存
                    if int(count) > sku.stock:
                        transaction.savepoint_rollback(save_id)
                        return JsonResponse({'res': 6, 'errmsg': '商品库存不足'})

                    # todo 更新商品的库存销量和库存
                    orign_stock = sku.stock
                    new_stock = orign_stock - int(count)
                    new_slales = sku.sales + int(count)

                    res = GoodsSKU.objects.filter(
                        id=sku_id, stock=orign_stock).update(
                        stock=new_stock, sales=new_slales)
                    if res == 0:
                        if i == settings.NUMBER_OF_FAILED_ORDER_TIMES - 1:
                            transaction.savepoint_rollback(save_id)
                            return JsonResponse({'res': 7, 'errmsg': '下单失败!'})
                        continue

                    # todo to订单信息表中添加一条记录
                    OrderGoods.objects.create(
                        order=order,
                        sku=sku,
                        count=int(count),
                        price=sku.price,
                    )

                    # todo 累加计算商品的订单的总数量和总价格
                    amount = sku.price * int(count)
                    total_count += int(count)
                    total_price += amount

                    break

            # todo 更新订单信息表中的商品的总数量和价格
            order.total_count = total_count
            order.total_price = total_price
            order.save()
        except Exception:
            transaction.savepoint_rollback(save_id)
            return JsonResponse({'res': 7, 'errmsg': '下单失败!'})
        # 提交
        transaction.savepoint_commit(save_id)
        # todo 清楚用户的购物车记录
        coon.hdel(cart_key, *sku_ids)

        return JsonResponse({'res': 5, 'message': '创建成功!'})


class OrderPayView(View):
    def post(self, request):
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '请先登录!'})

        order_id = request.POST.get('order_id')

        if not order_id:
            return JsonResponse({'res': 1, 'errmsg': '无效的订单id'})

        try:
            order = OrderInfo.objects.get(
                order_id=order_id, user=user, pay_method=3, order_status=1)
        except OrderInfo.DoesNotExist:
            return JsonResponse({'res': 2, 'errmsg': '订单错误！'})

        alipay = AliPay(
            appid=settings.ALIPAY_APP_ID,
            app_notify_url=None,  # 默认回调url
            app_private_key_path=settings.APP_PRIVATE_KEY_PATH,
            # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
            alipay_public_key_path=settings.ALIPAY_PUBLIC_KEY_PATH,
            sign_type="RSA2",  # RSA 或者 RSA2
            debug=True  # 默认False
        )
        total_pay = order.total_price + order.transit_price
        order_string = alipay.api_alipay_trade_page_pay(
            out_trade_no=order_id,
            total_amount=str(total_pay),
            subject='天天生鲜%s' % order_id,
            return_url=None,
            notify_url=None  # 可选, 不填则使用默认notify url
        )

        pay_url = 'https://openapi.alipaydev.com/gateway.do?' + order_string

        return JsonResponse({'res': 3, 'pay_url': pay_url})


class OrderCheckView(View):
    def post(self, request):
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '请先登录!'})

        order_id = request.POST.get('order_id')

        if not order_id:
            return JsonResponse({'res': 1, 'errmsg': '无效的订单id'})

        try:
            order = OrderInfo.objects.get(
                order_id=order_id, user=user, pay_method=3, order_status=1)
        except OrderInfo.DoesNotExist:
            return JsonResponse({'res': 2, 'errmsg': '订单错误！'})

        alipay = AliPay(
            appid=settings.ALIPAY_APP_ID,
            app_notify_url=None,  # 默认回调url
            app_private_key_path=settings.APP_PRIVATE_KEY_PATH,
            # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
            alipay_public_key_path=settings.ALIPAY_PUBLIC_KEY_PATH,
            sign_type="RSA2",  # RSA 或者 RSA2
            debug=True  # 默认False
        )

        # 调用支付宝的交易查询接口
        while True:
            response = alipay.api_alipay_trade_query(order_id)

            code = response.get('code')
            if code == '10000' and response.get(
                    'trade_status') == 'TRADE_SUCCESS':
                # 支付成功
                # 更新订单的状态
                train_no = response.get('trade_no')
                order.trade_no = train_no
                # 将支付状态改为待评价
                order.order_status = 4
                order.save()
                return JsonResponse({'res': 3, 'message': '支付成功!'})
            elif (code == '10000' and response.get('trade_status') == 'WAIT_BUYER_PAY') or code == '40004':
                # 等待支付
                sleep(5)
                continue
            else:
                # 支付失败
                return JsonResponse({'res': 4, 'errmsg': '支付失败!'})
