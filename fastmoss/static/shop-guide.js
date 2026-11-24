// Screenshots supplied by the user. Only the distributed copies are redacted.
const SHOP_DOCS={onboarding:'https://partner.tiktokshop.com/docv2/page/seller-developer-onboarding-onepager',seller:'https://partner.tiktokshop.com/docv2/page/seller-authorization-guide'};
const SHOP_STEPS=[
 {title:'在商家中心打开「应用」',text:'登录自己的 TikTok Shop 商家账号，点击左侧「应用」。'},
 {title:'点击「开发自己的应用」',text:'在应用商店右上角，点击「开发自己的应用」，进入 Partner Center。'},
 {title:'进入 Partner Center 工作台',text:'进入 Partner Center 工作台。如平台要求登录、验证主体或完成开发者入驻，请先按页面提示完成。'},
 {title:'打开 App & Service',text:'在 Partner Center 左侧选择 App & Service（应用与服务）。'},
 {title:'创建应用',text:'点击右上角 Create app & service（创建应用与服务）。'},
 {title:'选择 Custom 和卖家自用类别',text:'选择 Custom，再选择 Seller inhouse developer → TikTok Shop Seller。若账号没有这些选项，先完成平台要求的资格申请。'},
 {title:'填写应用名称',text:'给自己的应用起一个便于识别的名称，例如「我的店铺上架助手」。Logo 为选填。'},
 {title:'选择市场与卖家类型',text:'本工具的上架功能面向美国市场。根据自己的店铺确认本土 / 跨境类型；跨境主体所在地按实际填写。不要照抄不符合自己店铺的信息。'},
 {title:'填写回调地址并创建',text:'开启 Enable API，将 http://127.0.0.1:8765/ 完整复制到 Redirect URL（保留末尾斜杠），再点击 Create。新版软件在每台电脑上都使用这个固定地址；它指向当前电脑自身。授权浏览器和本软件须在同一台电脑运行，并保持软件开启。'},
 {title:'打开权限设置',text:'进入应用详情，点击 Manage scope（管理权限）。'},
 {title:'开启所需的七项权限',text:'核对 Global Category Information、Global Product Modify、Global Shop Information、Logistics Basic、Product Basic、Product Modify、Shop Authorized Information。以平台实际批准和授权返回的权限为准。'},
 {title:'发布应用',text:'点击右上角 Publish，核对市场后在弹窗中再次点击 Publish。若修改了已发布应用，使用 Publish changes，并完成平台提示的审核。'},
 {title:'复制三项内容，回到本软件连接',text:'① Copy authorization link → 授权链接；② App key → App Key；③ App secret → App Secret。分别粘贴到上方三个输入框，点击「保存并连接店铺」，再点击「复制授权链接」，粘贴到同一台电脑上紫鸟对应的店铺窗口中打开并确认授权。授权期间请保持本软件运行。'}
];
function shopGuideMarkup(index,escape){
 const step=SHOP_STEPS[index],src=`tutorial/connection/step-${String(index+1).padStart(2,'0')}.png?v=20261124-redacted`;
 return `<div class="sm-guide"><p class="sm-kicker">步骤 ${index+1} / 13</p><h3>${escape(step.title)}</h3><p>${escape(step.text)}</p><figure hidden><a href="${src}" target="_blank" rel="noopener" title="打开大图"><img src="${src}" alt="步骤${index+1}：${escape(step.title)}，按红框和箭头操作" onload="this.closest('figure').hidden=false"></a><figcaption>本机操作参考截图 · 点击图片放大查看</figcaption></figure></div>`;
}
