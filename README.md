# Secure & Trustworthy Agents (STAGE)

GitHub Pages 发布公开首页。组内资料由 Python 服务提供，服务端核验身份后才返回正文。组员和管理员使用同一个「组内登录」表单。组员填登记姓名和一卡通号，管理员填配置的姓名和管理密码。

## 本机使用

需要 Python 3.10 或更新版本。将三份 Word 源文件放在同一个目录，在仓库根目录执行下面的命令。

```powershell
python build_lab_site.py --source-dir "$env:USERPROFILE\Downloads"
python preview_server.py --set-admin "冯磊"
python preview_server.py
```

打开 [本机首页](http://127.0.0.1:8765/#login)。管理员密码在终端提示后输入。管理员登录后可在「组员账号管理」中补录缺少的一卡通号，或启用、停用组员账号。补录时请使用名单中登记的姓名对应的一卡通号。

导入程序读取《课题组成员与当前事项》《课题组管理细则》和《英文论文写作注意事项》。成员名单文件另有更新版时，可带上 `--roster-file` 指定该文件。导入完成后，重启服务以加载更新的组内资料。

组内正文及账号保存在 Git 忽略的 `.lab-data/lab_accounts.sqlite3` 内。名单的一卡通号用于生成账号校验值，组内页面省略卡号列。迁移或备份时请停止服务，将数据库保存在仅服务管理员可读取的目录。

## 在线部署

在成员可访问的服务器上放置网站代码及私有数据库，使用专用 HTTPS 域名和反向代理。先在该服务器导入文档并配置管理员，或迁移已有数据库。然后用下面的命令启动服务。

```powershell
python -m pip install -r requirements.txt
python preview_server.py --public-origin https://lab.example.edu
```

将示例域名替换成实际域名。HTTPS 模式由 Waitress 监听 `127.0.0.1:8765`，用同一台服务器上的反向代理转发请求。如果使用 Caddy，可以采用下面的配置。

```caddyfile
lab.example.edu {
    reverse_proxy 127.0.0.1:8765
}
```

配置好域名、TLS 和进程常驻之后，先验证组员和管理员均可登录，再将 GitHub Pages 的 `index.html` 中「组内登录暂未在线开放」替换为指向实际 HTTPS 地址 `/#login` 的链接。
