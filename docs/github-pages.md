# 静态网页与 GitHub Pages

网页从 `data/electricity_data.csv` 读取电量记录，导出为可直接托管的 HTML、CSS 和 JavaScript。构建网页只需 Flask 和 pandas，不会登录学校系统，也无需安装浏览器或 OCR 模型。原有 `python src/web_panel.py` 本地 Flask 面板仍可使用。

## 页面功能

- 显示剩余电量、最近 7 天日均用电、预计可用天数和最新数据时间。
- 提供 7 天、30 天、90 天、全部历史和自定义日期区间；可用鼠标或左右方向键查看读数。
- 日期选择栏在按钮下方展开，支持点击外部或 Esc 收起；切换曲线时从下方升起，尊重系统减少动画设置。
- 按目标天数和电价估算充值金额。默认 30 天、0.50 元/度仅为可修改的估算值，请按实际电价调整。
- 适配桌面与手机，时间统一显示为北京时间。

日均用电按最近 7 天内至少跨度一天的有效读数估算，电量增加不计入耗电。建议金额先扣除现有电量，再向上取整到 10 元；估算不进行实际充值。缺少记录时显示空状态。

## 首次启用自动发布

1. 在仓库 **Settings → Pages** 中，将 **Build and deployment → Source** 设为 **GitHub Actions**。
2. 在 **Settings → Secrets and variables → Actions → Variables** 中新增仓库变量 `ENABLE_PAGES`，值为 `true`。未设置时不会执行网站发布。
3. 在 **Actions → Deploy electricity dashboard → Run workflow** 中，选择默认分支并运行一次。
4. 完成后通过 Pages 设置或部署记录中的网址访问网站。默认地址为 `https://<用户名>.github.io/<仓库名>/`，不需要自定义域名。

之后，默认分支上的 **Auto Monitor Schedule** 成功结束、网页代码更新或 CSV 更新都会触发发布。网页读取最新 CSV；最新数据时间对应最后一次有效采集记录。刷新网页读取已发布内容，不触发新的学校系统查询。

发布会公开 CSV 中的电量记录及采集时间；启用前请确认记录适合公开。只上传生成的 `_site/`，不会上传登录配置、密码或日志。PR 检查使用测试中构造的记录，不运行学校登录，也不需要账号或邮件凭据。

需要自定义域名时，在 Pages 设置中自行绑定并配置 DNS。Actions 发布无需 `CNAME` 文件；证书就绪后可启用强制 HTTPS。参见 [GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) 与 [自定义域名说明](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site)。

## 本地生成与预览

```bash
python -m venv .venv-web
# macOS / Linux
source .venv-web/bin/activate
# Windows 可改用 .venv-web\Scripts\activate
python -m pip install -r requirements-web.txt
python src/build_site.py
python -m http.server 8000 --bind 127.0.0.1 --directory _site
```

打开 `http://127.0.0.1:8000/`。也可以用 `--csv-path /path/to/electricity_data.csv` 指定记录、用 `--output-dir /path/to/site` 指定输出目录。CSV 需含 `time`、`num` 列；时间无时区时按北京时间处理。文件尚未生成或为空时，仍可生成空状态页面。

验证方式：`python -m unittest discover -s tests_web` 和 `node --test tests_web/*.cjs`。
