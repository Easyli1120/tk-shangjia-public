# TikTok 官方依据与回调方案

核对日期：2026-11-20。文档与账户资格可能更新，软件内提供官方入口。

## 自用应用资格

自有店铺路线使用 Seller Developer / Custom App，不默认员工能创建 Public App。需要店铺所有者账号、主体审核及平台资格；子账号不适用。美国等市场的资格不能由软件授予。具体条件、市场入口、客户经理或平台资格限制以 [Seller Developer onboarding](https://partner.tiktokshop.com/docv2/page/seller-developer-onboarding-onepager) 和 [开发者类型](https://partner.tiktokshop.com/docv2/page/tts-developer-types) 为准。

自用 Custom App 不能等同于免审、全权限或可以服务任意商家。账户入驻、应用状态、权限批准、发布或 Publish changes 均须按平台要求完成。[应用审核](https://partner.tiktokshop.com/docv2/page/app-review-process)

## 回调研究结论

Seller 授权完成后，是浏览器跳转到登记的 Redirect URL 并携带 Code / State；这与 TikTok 服务端主动访问 localhost 不同。[Seller Authorization](https://partner.tiktokshop.com/docv2/page/seller-authorization-guide)

官方 [服务器开发示例](https://github.com/tiktok/ttspc-server-sample) 包含 HTTPS localhost 测试方式。这只能证明官方提供过本地开发示例，不能证明所有正式 Seller App 接受 HTTP 本地地址。没有找到可用于保证全部正式账户的统一结论，因此软件把当前本机 HTTP 地址作为候选，要求使用者在自己的 Partner Center 实际保存验证。如果平台拒绝，必须准备自己控制的 HTTPS 地址；不能把 localhost 包装成公网 HTTPS。

软件不能读取 Partner Center 中登记的 Redirect URL，也不能在无授权 Code / Token 时用“输入非空”证明密钥正确。三项粘贴入口无需额外勾选；高级设置中的手动确认不作为成功证据。真实匹配仍待回调验证。打开官方授权页、收到浏览器跳转、Token 有效、获取店铺成功、商品权限具备是不同的验证项。

## 自有 HTTPS 方案（仅当平台要求时）

每位使用者自行准备：域名、有效受信任证书、能连到自己电脑的反向代理或隧道服务。不得关闭 TLS 证书校验。程序不内置、不自动安装第三方隧道，也没有开发者中央回调服务器。

只把以下路径转发到配置页显示的**专用回调端口**，不能转发主网页端口：

- `/api/shop/callback`
- `/api/shop/callback-health`

保留 HTTPS 原始 Host，设置 `X-Forwarded-Proto: https`，其余路径拒绝。关闭代理针对 callback 的查询字符串访问日志，避免记录一次性 Code。代理配置示例（端口必须替换为软件实际显示值）：

```nginx
location = /api/shop/callback {
    access_log off;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_pass http://127.0.0.1:CALLBACK_PORT;
}
location = /api/shop/callback-health {
    access_log off;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_pass http://127.0.0.1:CALLBACK_PORT;
}
location / { return 404; }
```

这段只说明转发边界，不是已经部署的 HTTPS 服务。软件会通过随机健康标识检查 HTTPS 地址是否真正连接本实例；失败时禁止开始授权。没有自身可用 HTTPS 服务且平台不接受本地地址时，不能完成绑定。此条件无法在“不提供中央服务”的前提下由安装包凭空解决。

## 协议与权限

Token endpoint 使用 `authorized_code` grant_type；返回到期字段为 Unix 时间戳，按服务器实际返回值处理。[Authorization overview](https://partner.tiktokshop.com/docv2/page/authorization-overview-202407)

| 功能 | 实际所需权限 | 官方文档 |
| --- | --- | --- |
| 已授权店铺 | seller.authorization.info（平台另列可选履约授权范围） | [Get Authorized Shops](https://partner.tiktokshop.com/docv2/page/get-authorized-shops-202309) |
| 商品查询 / 图片上传 / 上架检查 | seller.product.basic | [Search Products](https://partner.tiktokshop.com/docv2/page/search-products-202309)、[Upload Product Image](https://partner.tiktokshop.com/docv2/page/upload-product-image-202309)、[Check Product Listing](https://partner.tiktokshop.com/docv2/page/check-product-listing-202309) |
| 本土创建 / 部分编辑 | seller.product.write | [Create Product](https://partner.tiktokshop.com/docv2/page/create-product-202309)、[Partial Edit](https://partner.tiktokshop.com/docv2/page/partial-edit-product-202509) |
| 店铺仓库 | seller.logistics | [Warehouse List](https://partner.tiktokshop.com/docv2/page/get-warehouse-list-202309) |
| 全球仓库 / 跨境卖家权限 | seller.shop.info | [Global Warehouses](https://partner.tiktokshop.com/docv2/page/get-global-seller-warehouses-202309)、[Global Permission](https://partner.tiktokshop.com/docv2/page/get-global-product-permission-202309) |
| 全球类目、规则、属性 | seller.global_product.category.info | [Global Categories](https://partner.tiktokshop.com/docv2/page/get-global-categories-202309) |
| 全球商品读取 | seller.global_product.info | [Get Global Product](https://partner.tiktokshop.com/docv2/page/get-global-product-202309) |
| 全球商品创建 / 发布 | seller.global_product.write | [Create Global Product](https://partner.tiktokshop.com/docv2/page/create-global-product-202309)、[Publish Global Product](https://partner.tiktokshop.com/docv2/page/publish-global-product-202309) |
| 当前店铺全球上架方式 | seller.product.basic | [Global Listing Rules](https://partner.tiktokshop.com/docv2/page/get-global-listing-rules-202507) |

平台仍可因类目资格、品牌授权、仓库、主体或其他规则拒绝具体商品。拥有权限不代表任意商品都能发布。
