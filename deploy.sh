#!/usr/bin/env bash
# ============================================================================
# deploy.sh — 学术主页整站一键部署到 GitHub Pages（根域名 Hawkoding.github.io）
# ----------------------------------------------------------------------------
# 用法:
#   bash deploy.sh              正常部署（需要 GH_TOKEN 环境变量）
#   bash deploy.sh --dry-run    离线演练: 把整站复制到临时目录，完整执行
#                               git init/add/commit，不联网、不推送、
#                               结束后不留任何文件
#
# 环境变量:
#   GH_TOKEN      必填（--dry-run 除外）。GitHub Personal Access Token:
#                 经典 PAT 勾选 repo 权限; 细粒度 PAT 需授予
#                 Administration / Contents / Pages 写权限
#                 安全约定: token 只出现在 curl / git 的请求头里，
#                 不写入任何文件、远程地址或日志。
#   REPO_NAME     可选。仓库名, 默认 Hawkoding.github.io（用户名仓库,
#                 站点将发布在 https://<用户名>.github.io/ 根路径下）
#   FORCE_PUSH    可选。=1 时推送附加 --force（覆盖远程 main 分支历史）
#   POLL_INTERVAL 可选。站点就绪轮询间隔（秒）, 默认 10
#   POLL_MAX      可选。站点就绪轮询最大次数, 默认 45
#
# 流程:
#   0) 离线检查: bash 语法、整站关键文件齐全、token 未泄漏进站点文件
#   1) 读取 GH_TOKEN / REPO_NAME
#   2) 调 GitHub API 获取用户名, 自动配置 git 提交身份（noreply 邮箱）
#   3) 若仓库不存在则创建（公开仓库, GitHub Free 的 Pages 要求）
#   4) 在本目录 git init, 整站 add/commit（含 blog/、cv/ 等全部文件）,
#      通过临时请求头认证推送到远程 main 分支
#   5) 启用 GitHub Pages（main 分支 / 根目录）
#   6) 轮询 https://<用户名>.github.io/（根域名）直至返回 200
#
# 说明:
#   · 默认仓库 Hawkoding.github.io 恰为 <用户名>.github.io, 站点发布在
#     根路径 https://hawkoding.github.io/, 脚本自动识别, 无需修改任何
#     文件（站内链接全部是相对路径）。
#   · 若 REPO_NAME 改为普通仓库名, 站点自动落到 /<仓库名>/ 子路径。
#   · 本脚本自身随整站一并入库, 克隆仓库后可再次运行以更新站点。
# ============================================================================

set -u
umask 022

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)
REPO_NAME="${REPO_NAME:-Hawkoding.github.io}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
POLL_MAX="${POLL_MAX:-45}"
DRY_RUN=0
GH_TOKEN="${GH_TOKEN:-}"

# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
die() { printf '\n[错误] %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$1"; if [ $# -ge 2 ]; then printf '    %s\n' "$2"; fi; }

usage() {
  sed -n '2,37p' "${BASH_SOURCE[0]:-$0}"
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage ;;
    *) die "未知参数: $arg（用法见 --help）" ;;
  esac
done

command -v git   >/dev/null 2>&1 || die "未找到 git, 请先安装: https://git-scm.com"
command -v curl  >/dev/null 2>&1 || die "未找到 curl, 请先安装 curl"

# ----------------------------------------------------------------------------
# 第 0 步: 整站结构检查（离线, 两种模式都执行）
# ----------------------------------------------------------------------------
step "整站结构检查" "目录: ${SCRIPT_DIR}"

REQUIRED_FILES=(
  "index.html"
  ".nojekyll"
  "blog/index.html"
  "blog/blog.css"
  "cv/cv.pdf"
  "cv/cv_cn.pdf"
)
for f in "${REQUIRED_FILES[@]}"; do
  [ -f "${SCRIPT_DIR}/${f}" ] || die "缺少站点文件: ${f}
    请在 deploy.sh 所在目录运行本脚本; 若你已确认删除某部分站点,
    可同步精简脚本顶部的 REQUIRED_FILES 列表。"
done

# 占位 PDF 一并占位检查: 主页确实链接了它们
if grep -q '</html>' "${SCRIPT_DIR}/index.html"; then :; else
  die "index.html 似乎不完整（未找到 </html>）"
fi

SITE_FILE_COUNT=$(find "${SCRIPT_DIR}" -type f -not -path '*/.git/*' | wc -l | tr -d ' ')
printf '    关键文件齐全, 目录内共 %s 个文件（不含 .git）\n' "${SITE_FILE_COUNT}"

# ----------------------------------------------------------------------------
# 离线演练模式: 临时目录内完整走一遍 git init/add/commit
# ----------------------------------------------------------------------------
if [ "${DRY_RUN}" = "1" ]; then
  step "离线演练" "整站复制到临时目录, 完整执行 git init/add/commit（不联网、不推送）"

  TMP_DIR=$(mktemp -d 2>/dev/null) || die "无法创建临时目录"
  trap 'rm -rf "${TMP_DIR}"' EXIT INT TERM

  # 复制整站（排除 .git 与系统杂物）
  ( cd "${SCRIPT_DIR}" && find . -mindepth 1 -maxdepth 1 \
      ! -name '.git' ! -name '.DS_Store' ! -name 'Thumbs.db' \
      -exec cp -R -p {} "${TMP_DIR}/" \; ) || die "整站复制失败"

  # 临时目录里同样验证关键文件
  for f in "${REQUIRED_FILES[@]}"; do
    [ -f "${TMP_DIR}/${f}" ] || die "离线演练失败: 临时目录缺少 ${f}"
  done

  # token 泄漏检查（对将要提交的全部文件）
  if [ -n "${GH_TOKEN}" ]; then
    if grep -rl -- "${GH_TOKEN}" "${TMP_DIR}" >/dev/null 2>&1; then
      die "检测到 GH_TOKEN 出现在站点文件中, 已中止。请先移除再部署。"
    fi
  fi

  git -C "${TMP_DIR}" init -q 2>/dev/null || die "git init 失败"
  git -C "${TMP_DIR}" symbolic-ref HEAD refs/heads/main
  git -C "${TMP_DIR}" config user.name  "site-deployer"
  git -C "${TMP_DIR}" config user.email "deploy@localhost"
  printf '.DS_Store\nThumbs.db\n*.swp\n' > "${TMP_DIR}/.git/info/exclude"
  git -C "${TMP_DIR}" add -A || die "git add 失败"
  git -C "${TMP_DIR}" commit -q -m "Deploy site (${REPO_NAME}) — dry-run rehearsal" \
    || die "git commit 失败"

  COMMIT_FILES=$(git -C "${TMP_DIR}" ls-files | wc -l | tr -d ' ')
  step "演练结果" "本次提交共覆盖 ${COMMIT_FILES} 个文件:"
  git -C "${TMP_DIR}" ls-files | sed 's/^/    /'
  printf '\n离线演练通过: 整站（%s 个文件）已全部纳入一次提交。\n' "${COMMIT_FILES}"
  exit 0
fi

# ----------------------------------------------------------------------------
# 第 1 步: 读取 token 并自检
# ----------------------------------------------------------------------------
step "读取 GH_TOKEN" "token 仅用于请求头, 不写入文件 / 远程地址 / 日志"

[ -n "${GH_TOKEN}" ] || die "未设置 GH_TOKEN。
  请先创建 PAT（GitHub → Settings → Developer settings →
  Personal access tokens）, 然后执行:
      export GH_TOKEN=ghp_xxxxxxxxxxxx   # 仅当前终端会话有效"

if grep -rl -- "${GH_TOKEN}" "${SCRIPT_DIR}" --exclude-dir=.git >/dev/null 2>&1; then
  die "检测到 GH_TOKEN 出现在站点文件中, 已中止。请先移除再部署。"
fi

api() {  # api <方法> <路径> [JSON body]
  local method=$1 path=$2 body=${3:-}
  if [ -n "${body}" ]; then
    curl -sS -X "${method}" \
      -H "Authorization: Bearer ${GH_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      -d "${body}" "https://api.github.com${path}"
  else
    curl -sS -X "${method}" \
      -H "Authorization: Bearer ${GH_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "https://api.github.com${path}"
  fi
}

# ----------------------------------------------------------------------------
# 第 2 步: 获取用户名, 配置 git 提交身份
# ----------------------------------------------------------------------------
step "获取 GitHub 用户名" "GET /user"

USER_JSON=$(api GET /user) || die "GitHub API 请求失败, 请检查网络与 token"
GH_LOGIN=$(printf '%s' "${USER_JSON}" | sed -n 's/.*"login"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
[ -n "${GH_LOGIN}" ] || die "无法从 API 响应解析用户名。
  常见原因: token 无效或过期。响应片段:
  $(printf '%s' "${USER_JSON}" | head -c 300)"

printf '    当前用户: %s\n' "${GH_LOGIN}"

REPO_URL="https://github.com/${GH_LOGIN}/${REPO_NAME}.git"

# 仓库恰好是 <用户名>.github.io 时, 站点发布在根路径而非子路径
if [ "${REPO_NAME}" = "${GH_LOGIN}.github.io" ]; then
  SITE_URL="https://${GH_LOGIN}.github.io/"
else
  SITE_URL="https://${GH_LOGIN}.github.io/${REPO_NAME}/"
fi
printf '    仓库地址: %s\n    站点地址: %s\n' "${REPO_URL}" "${SITE_URL}"

# ----------------------------------------------------------------------------
# 第 3 步: 仓库不存在则创建
# ----------------------------------------------------------------------------
step "检查 / 创建仓库" "${REPO_NAME}"

REPO_JSON=$(api GET "/repos/${GH_LOGIN}/${REPO_NAME}")
if printf '%s' "${REPO_JSON}" | grep -q '"id"'; then
  printf '    仓库已存在, 跳过创建\n'
else
  printf '    仓库不存在, 创建公开仓库 %s ...\n' "${REPO_NAME}"
  CREATE_JSON=$(api POST /user/repos \
    "{\"name\":\"${REPO_NAME}\",\"description\":\"Personal academic homepage and blog, deployed via GitHub Pages\",\"homepage\":\"${SITE_URL}\",\"private\":false,\"has_issues\":true}")
  printf '%s' "${CREATE_JSON}" | grep -q '"id"' \
    || die "创建仓库失败。响应片段:
  $(printf '%s' "${CREATE_JSON}" | head -c 300)"
  printf '    仓库创建成功\n'
fi

# ----------------------------------------------------------------------------
# 第 4 步: 整站提交并推送
# ----------------------------------------------------------------------------
step "整站提交" "git init / add -A / commit（含 blog/、cv/ 全部文件）"

git -C "${SCRIPT_DIR}" init -q 2>/dev/null || die "git init 失败"
git -C "${SCRIPT_DIR}" symbolic-ref HEAD refs/heads/main
if ! git -C "${SCRIPT_DIR}" config user.name >/dev/null 2>&1; then
  git -C "${SCRIPT_DIR}" config user.name  "${GH_LOGIN}"
  git -C "${SCRIPT_DIR}" config user.email "${GH_LOGIN}@users.noreply.github.com"
fi
mkdir -p "${SCRIPT_DIR}/.git/info"
printf '.DS_Store\nThumbs.db\n*.swp\n' > "${SCRIPT_DIR}/.git/info/exclude"

# 远程地址不含 token: 推送认证走临时请求头（-c 仅对本次命令生效, 不落盘）
if git -C "${SCRIPT_DIR}" remote get-url origin >/dev/null 2>&1; then
  git -C "${SCRIPT_DIR}" remote set-url origin "${REPO_URL}"
else
  git -C "${SCRIPT_DIR}" remote add origin "${REPO_URL}"
fi

git -C "${SCRIPT_DIR}" add -A || die "git add 失败"

if git -C "${SCRIPT_DIR}" diff --cached --quiet HEAD 2>/dev/null; then
  printf '    没有待提交的改动, 沿用现有提交\n'
else
  git -C "${SCRIPT_DIR}" commit -q -m "Deploy site to GitHub Pages ($(date '+%Y-%m-%d %H:%M:%S'))" \
    || die "git commit 失败"
fi

COMMIT_FILES=$(git -C "${SCRIPT_DIR}" ls-files | wc -l | tr -d ' ')
printf '    本次纳入版本控制的文件数: %s\n' "${COMMIT_FILES}"

PUSH_FLAGS="-u origin main"
if [ "${FORCE_PUSH:-0}" = "1" ]; then PUSH_FLAGS="--force origin main"; fi

AUTH_HEADER=$(printf 'x-access-token:%s' "${GH_TOKEN}" | base64 | tr -d '\n')
step "推送到远程" "git push ${PUSH_FLAGS}（认证经请求头注入, token 不落盘）"

git -C "${SCRIPT_DIR}" \
  -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${AUTH_HEADER}" \
  push ${PUSH_FLAGS} 2>&1 | sed "s/${GH_TOKEN}/***/g" \
  || die "推送失败。常见原因: 远程 main 已有不同历史（可用 FORCE_PUSH=1 重试）、
  token 权限不足（经典 PAT 需勾选 repo）、或网络受限。"

# ----------------------------------------------------------------------------
# 第 5 步: 启用 GitHub Pages
# ----------------------------------------------------------------------------
step "启用 GitHub Pages" "main 分支 / 根目录"

PAGES_JSON=$(api POST "/repos/${GH_LOGIN}/${REPO_NAME}/pages" \
  '{"source":{"branch":"main","path":"/"}}')

if printf '%s' "${PAGES_JSON}" | grep -q '"html_url"'; then
  API_SITE_URL=$(printf '%s' "${PAGES_JSON}" | sed -n 's/.*"html_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
  # 以 Pages 实际返回的地址为准（兼容根路径仓库等特殊情况）
  [ -n "${API_SITE_URL}" ] && SITE_URL="${API_SITE_URL}"
  printf '    Pages 已启用: %s\n' "${SITE_URL}"
elif printf '%s' "${PAGES_JSON}" | grep -qi 'already\|exist'; then
  printf '    Pages 此前已启用, 跳过\n'
else
  printf '    （警告）启用 Pages 返回异常, 请手动前往 Settings → Pages 检查:\n'
  printf '    %s\n' "$(printf '%s' "${PAGES_JSON}" | head -c 300)"
fi

# ----------------------------------------------------------------------------
# 第 6 步: 轮询站点直至 200
# ----------------------------------------------------------------------------
step "等待站点就绪" "轮询 ${SITE_URL}（间隔 ${POLL_INTERVAL}s, 最多 ${POLL_MAX} 次）"
OK=0
i=1
while [ "${i}" -le "${POLL_MAX}" ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${SITE_URL}") || code=000
  if [ "${code}" = "200" ]; then
    OK=1
    break
  fi
  printf '    第 %s/%s 次: HTTP %s, 继续等待...\n' "${i}" "${POLL_MAX}" "${code}"
  sleep "${POLL_INTERVAL}"
  i=$((i + 1))
done

if [ "${OK}" = "1" ]; then
  cat <<EOF

============================================================
部署成功! 整站已上线:
    ${SITE_URL}
============================================================
EOF
  exit 0
else
  TIMEOUT_SEC=$((POLL_INTERVAL * POLL_MAX))
  cat >&2 <<EOF
警告: 文件已推送且 Pages 已启用, 但站点在约 ${TIMEOUT_SEC} 秒内未返回 200。
GitHub 首次构建偶尔较慢（最长约 10 分钟）, 请稍后直接访问:
    ${SITE_URL}
构建状态可查看: https://github.com/${GH_LOGIN}/${REPO_NAME}/settings/pages
EOF
  exit 5
fi
