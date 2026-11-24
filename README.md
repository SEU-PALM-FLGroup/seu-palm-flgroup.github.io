# 冯磊课题组网站

`index.html` 是网站首页，也是 GitHub Pages 从 `main` 发布的入口。GitHub Pages 展示课题组公开信息。本机服务在同一个首页提供组员和管理员登录，通过验证后显示组内资料。

## 本机运行

需要 Python 3.10 或更新版本。首次生成资料时，将三份 Word 原文件放在同一个目录，然后在仓库根目录运行以下命令。默认读取当前用户的 `Documents` 文件夹，也可在命令中指定 `--source-dir`。

```powershell
python build_lab_site.py
python preview_server.py
```

[打开本机首页](http://127.0.0.1:8765/)。在首页的「组员登录」区域登录，或点击「切换到管理员登录」。

首次配置管理员时，在仓库根目录运行 `python preview_server.py --set-admin "冯磊"`，并在终端提示后输入密码。

Word 文档生成的正文和本机账号数据库保存在 `.lab-data/`，由 Git 忽略。导入新版本 Word 文档后，重新运行生成命令并重启服务以同步登录名单。需要迁移本机账号时，请在停止服务后备份 `.lab-data/`。
