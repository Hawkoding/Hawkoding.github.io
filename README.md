# Hawkoding.github.io · 学术主页整站（GitHub Pages 根域名部署）

一个不依赖任何构建工具的纯静态个人主页整站：单页主页 + 博客 + 简历占位文件 +
一键部署脚本。主页内容为 Hawk 的真实信息；博客「随想录 / Hawk's Notes」已上线，
主页设**两个入口**（导航栏 Blog 胶囊按钮 + 页眉下深色精选卡片）。
`cv/` 内 PDF 仍为**占位文件**，待简历就绪后替换并在页眉恢复 CV 链接
（相关样式均已保留在 `index.html` 中）。

默认仓库名为 `Hawkoding.github.io`（用户名仓库），站点直接发布在根域名
`https://hawkoding.github.io/`——站内全部链接使用相对路径，放在根路径或
子路径下都不会破图、不会断链；若 `REPO_NAME` 改为普通仓库名，站点会自动
发布在 `https://<你的用户名>.github.io/<仓库名>/` 子路径下，同样无需改动文件。

## 目录结构

| 文件 | 说明 |
| ---- | ---- |
| `index.html` | 主页：单文件、样式内联，内容为Hawk真实信息 |
| `blog/index.html` | 博客列表页：hero / 侧栏文案手写；文章卡片与「栏目」由 md2blog.py 在标记内生成 |
| `blog/blog.css` | 博客样式（与主页同源风格） |
| `blog/posts/` | 文章页：由 `tools/md2blog.py` 从 `database/` 生成（勿手改）+ 3 篇占位示例 |
| `blog/feed.xml` | RSS 订阅源：`ITEMS` 区域由 md2blog.py 生成 |
| `database/` | 博客 markdown 源稿：子文件夹 = 分类，文件名 `YYYYMMDD 标题.md` |
| `tools/md2blog.py` | 自研转换工具：md → 文章页，并重写列表页与 RSS（幂等、可 `--dry-run`） |
| `tools/deploy.sh` | 一键部署脚本（macOS / Linux / Windows Git Bash） |
| `cv/cv.pdf`、`cv/cv_cn.pdf` | 简历占位 PDF，与主页 `cv/…` 链接对应；**请替换为你自己的简历** |
| `.nojekyll` | 让 GitHub Pages 跳过 Jekyll 处理，文件原样发布 |
| `README.md` | 本说明 |

## 方式一：一键部署（推荐）

### 前置条件

1. 已安装 `git` 与 `curl`；
2. 一个 GitHub Personal Access Token（PAT），创建入口：
   `GitHub → Settings → Developer settings → Personal access tokens`
   - 经典（Classic）PAT：勾选 `repo` 权限（推荐，最简单）；
   - 细粒度（Fine-grained）PAT：需授予 `Administration`（写）、`Contents`（写）、
     `Pages`（写）权限，分别用于创建仓库、推送文件与启用 Pages。

### 操作步骤

```bash
export GH_TOKEN=ghp_xxxxxxxxxxxx     # 必填：你的 PAT（仅当前终端会话有效）
# REPO_NAME=其他名字 bash tools/deploy.sh  # 可选：默认仓库名为 Hawkoding.github.io

bash tools/deploy.sh
```

脚本会自动完成：创建公开仓库 `Hawkoding.github.io` → 整站提交推送（含
`blog/`、`cv/` 全部文件）→ 启用 Pages（main 分支）→ 轮询
`https://hawkoding.github.io/` 直至可访问。安全约定：**token 只用于请求头，
不写入任何文件、远程地址或日志**。

可以先离线演练（不联网、不推送、不留临时文件）：

```bash
bash tools/deploy.sh --dry-run    # 在临时目录完整走一遍 git init/add/commit 并列出全部提交文件
```

## 方式二：手动部署（不使用脚本）

1. 在 GitHub 上新建**公开**仓库，命名为 `Hawkoding.github.io`
   （用户名仓库，GitHub Free 账号的 Pages 仅支持公开仓库）；
2. 在本目录执行：

```bash
git init
git symbolic-ref HEAD refs/heads/main
git add -A
git commit -m "Init site"
git remote add origin https://github.com/Hawkoding/Hawkoding.github.io.git
git push -u origin main
```

3. 打开 `https://github.com/Hawkoding/Hawkoding.github.io` →
   **Settings → Pages → Source** 选择 `main` 分支（目录保持 `/root`）；
4. 等待 1–10 分钟，访问 `https://hawkoding.github.io/`。

也可以完全不碰命令行：在 GitHub 仓库页面用 **Add file → Upload files** 把本目录
内全部文件与文件夹拖进去提交（`.nojekyll` 为隐藏文件，网页上传时需单独拖入或
后续用命令补上），再按第 3 步启用 Pages。

## 自定义内容

- **主页**：打开 `index.html`，全文搜索「你的」逐项替换（姓名、学校、导师、
  研究方向、项目、论文……）；主题色改 `:root { --accent: #2c3e6b; }`；
  首字母头像 / 色块校徽按文件内注释替换为 `<img>` 真实图片。
- **博客**：`blog/index.html` 的品牌名、hero 文案、侧栏「关于这里 / 关注主题」
  手写编辑（位于 `<!-- POSTS:BEGIN/END -->`、`<!-- TOPICS:BEGIN/END -->`
  标记之外，重跑工具不会覆盖）；文章卡片与侧栏「栏目」由 md2blog.py 生成。
- **发布新文章**：
  1. 把 md 放进 `database/<分类>/`，文件名 `YYYYMMDD 标题.md`
     （标题取自文件名，无需 frontmatter；行尾双空格 = 硬换行）；
  2. `python tools/md2blog.py`（可选 `--dry-run` 预览、`--single 片段` 只转一篇）；
  3. `bash tools/deploy.sh` 推送上线。
  · 分类 = database 子文件夹名；`![[图片]]` 会被跳过；文章页文件名
    「日期-拼音slug.html」由工具生成，重复运行结果一致。
- **简历**：用自己的 PDF 覆盖 `cv/cv.pdf`（英文）与 `cv/cv_cn.pdf`（中文）；
  如需增删，同步修改 `index.html` 顶部的 `cv/…` 链接。
- **社交分享卡片**：各页 `<head>` 中被注释掉的 `og:image` / `twitter:*` 标签，
  在上传封面图（如 `images/og-cover.png`）后取消注释即可——地址已按本站根域名
  `https://hawkoding.github.io/…` 写好，必须是**绝对 URL**。

## 常见问题

- **访问 404**：首次构建需要 1–10 分钟；确认 Settings → Pages 的 Source 已设为
  `main` 分支且文件确在 `main` 分支根目录。
- **样式或页面打不开**：本站链接全部为相对路径，根路径/子路径部署均适用；
  请保持相对写法，不要改成以 `/` 开头的站根绝对路径——那在日后迁回
  `/<仓库名>/` 子路径部署时会指向错误位置。
- **图标不显示**：Font Awesome 图标通过 CDN 加载，需要浏览器联网。
- **为什么需要 `.nojekyll`**：GitHub Pages 默认经 Jekyll 处理，会忽略下划线
  开头的文件/目录（如 `blog/posts/_template.html`）；放置 `.nojekyll` 可让
  所有文件原样发布。
- **推送被拒绝**：远程 `main` 已有不同历史。确认可用本目录覆盖后，执行
  `FORCE_PUSH=1 bash tools/deploy.sh`（手动方式则 `git push --force -u origin main`）。
- **用户名仓库（当前默认）**：仓库名 `<你的用户名>.github.io` 时站点发布在
  `https://<你的用户名>.github.io/` 根路径（无子路径），脚本自动识别；
  本仓库 `Hawkoding.github.io` 即属此类。
- **免费版限制**：GitHub Free 账号的 Pages 仅支持公开仓库。
