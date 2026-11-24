# TK 上架助手 · TikTok Shop 美国站上架工作台

**从一批 TikTok 商品链接，到一批准备提交的美国站商品。** 在同一个本地工作台里采集商品资料、编辑英文详情和 SKU、核对上架问题，再把确认好的商品提交到自己的 TikTok Shop。

[快速开始](#快速开始) · [从采集到上架](#从采集到上架) · [反馈问题](https://github.com/Easyli1120/tk-shangjia-public/issues)

**如果这个项目帮你少开几个页面、少返工一轮，欢迎在 GitHub 右上角点个 ⭐ Star。** [TK 上架助手](https://github.com/Easyli1120/tk-shangjia-public)

## 能做什么

| 环节 | 工作台功能 |
| --- | --- |
| 批量采集 | 粘贴 TikTok Shop 商品链接，或导入符合格式的 CSV / XLSX。采集队列保存页面提供的商品图、规格图、详情图、尺码表和 SKU，并标明缺失资料。 |
| 商品编辑 | 直接修改英文标题和详情，管理图片、上架售价、实际库存和变体。可暂时排除零库存规格，也能恢复。 |
| 上架前检查 | 后台交叉核对面料，依据一致时补全平台属性，信息冲突时展示依据供人工确认。审核清单逐件列出图片、类目、品牌、尺码表和库存等问题，可在清单中修改部分字段并复检。 |
| 店铺提交与跟踪 | 使用自己的 TikTok Shop 授权，选择目标店铺，确认后提交。通过「发布记录」查看审核中、在售、失败或待核对的状态。 |

## 快速开始

从源码运行前请安装 Git。

### macOS

安装 Python 3.12 和 Google Chrome。需要本地图片文字识别时，安装 Xcode Command Line Tools。

```bash
git clone https://github.com/Easyli1120/tk-shangjia-public.git
cd tk-shangjia
python3.12 -m venv .venv
.venv/bin/python -m pip install -r fastmoss/requirements.txt
.venv/bin/python fastmoss/desktop.py
```

### Windows

安装包发布后可从 [GitHub Releases](https://github.com/Easyli1120/tk-shangjia-public/releases) 下载。也可以按[构建指南](fastmoss/docs/Windows构建与员工准备清单.md)自行打包。安装成功后，从桌面打开「TK 上架助手」。

从源码运行需要 Python 3.12 x64 和 Google Chrome 或 Microsoft Edge。在 PowerShell 中运行下面的命令。

```powershell
git clone https://github.com/Easyli1120/tk-shangjia-public.git
cd tk-shangjia
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r fastmoss\requirements.txt
.\.venv\Scripts\python.exe fastmoss\desktop.py
```

启动后会打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。这是当前电脑的地址，采集和授权期间请保持程序运行。若 8765 被占用，先退出占用端口的程序，再启动工作台。

## 从采集到上架

1. **采集商品。** 在「数据上传」粘贴商品链接，点击「开始采集」，或上传商品表格。查看采集进度和缺失原因。遇到验证码时，在采集窗口手动完成，队列随后继续。详见[采集说明](fastmoss/docs/tiktok-collection.md)。
2. **连接自己的店铺。** 在「店铺管理」按 13 步教程配置自己的 Partner Center 应用。粘贴授权链接、App Key 和 App Secret，点击「保存并连接店铺」，再复制授权链接到同一台电脑上已登录目标店铺的浏览器完成确认。若平台要求 HTTPS 回调，按[回调配置说明](fastmoss/docs/TikTok官方依据与回调方案.md)配置自己的地址。
3. **准备上架资料。** 在「竞品库」勾选商品，点击「一键上架」，选择目标店铺。修改英文文案，配置实际售价、库存和仓库，使用有权发布的素材，并核对面料和尺码。尺码表可以手动填写或上传图片，识别结果需核对尺寸和单位。
4. **检查并提交。** 在审核清单处理标出的问题，修改后复检。点击「整批检查并上架」完成最终检查，再点击「确认上架」提交当前批次。真实发布需要店铺具备相应的商品权限，平台审核结果可在「发布记录」查看。

## 常见问题

- **可以先试用吗？** 可以先运行工作台、采集商品和编辑英文资料。向 TikTok Shop 提交商品前，需要连接有商品操作权限的美国站店铺。
- **面料为什么要我确认？** 标题、详情和采集属性冲突，或资料不足时，工作台会提示人工核对。请以实际商品或供应商提供的成分为准。
- **需要 AI 密钥吗？** 英文编辑和上架流程可以直接使用。自动识别尺码表等可选能力可能需要另行配置模型，也可手动完成。
- **数据存在哪？** macOS 源码运行默认使用 `fastmoss/data/`，Windows 默认使用当前用户的 `%LOCALAPPDATA%\TKShangjia`。采集结果、凭证和浏览器会话保存在本机。

## 开发验证

在仓库根目录完成依赖安装后，进入 `fastmoss` 执行 Python 回归测试。前端检查需要 Node.js，可用 `node --test tests/*.cjs` 执行。

```bash
cd fastmoss
../.venv/bin/python -m unittest discover -s tests
```

复现问题时，欢迎在 Issue 附上操作步骤、系统版本和脱敏后的截图。 [提交反馈](https://github.com/Easyli1120/tk-shangjia-public/issues)
