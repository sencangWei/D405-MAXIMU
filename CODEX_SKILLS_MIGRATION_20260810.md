# Codex Skills 迁移清单

统计时间：2026-08-10（Asia/Shanghai）

当前基础环境：

- Codex CLI：`0.145.0`
- Node.js：`22.14.0`
- npm：`10.9.2`
- oh-my-codex：`0.18.15`
- agentmemory：`0.9.27`

## 统计结论

- Codex 内置 Skills：6 个。新电脑安装相近版本 Codex 后自带，不要手工复制 `.system`。
- 本机独立 Skills：16 个。已另外打包为 `codex_local_skills_20260810.tar.gz`。
- 已安装并启用的插件：4 个，共提供 70 个插件 Skills。
- 可复现安装集合共 92 个 Skill 目录；插件命名空间下允许与独立 Skill 同名。
- 缓存目录、源码工作副本和旧版本副本没有重复计数。

## 一、本机独立 Skills（16 个，需要迁移）

1. `anysearch`
2. `context-compression`
3. `context-degradation`
4. `crawl4ai`
5. `filesystem-context`
6. `karpathy-guidelines`
7. `multi-agent-patterns`
8. `piper-closed-loop-cube-stacking`
9. `planning-with-files`
10. `requesting-code-review`
11. `robotics-hil-acceptance`
12. `robotics-ros2-engineering`
13. `systematic-debugging`
14. `ubuntu-robotics-release`
15. `verification-before-completion`
16. `vla-training-reproducibility`

安装到另一台 Linux 电脑：

```bash
mkdir -p ~/.codex/skills
tar xzf codex_local_skills_20260810.tar.gz -C ~/.codex/skills
```

如果另一台电脑的用户名不是 `robot`，检查并修改：

```bash
sed -n '1,20p' ~/.codex/skills/anysearch/runtime.conf
```

其中命令路径应指向新电脑实际的 `~/.codex/skills/anysearch/scripts/anysearch_cli.py`。

## 二、Codex 内置 Skills（6 个，无需迁移）

1. `imagegen`
2. `openai-docs`
3. `plugin-creator`
4. `review-agent`
5. `skill-creator`
6. `skill-installer`

## 三、已启用插件及其 Skills（70 个）

### superpowers@openai-curated（14 个）

`brainstorming`、`dispatching-parallel-agents`、`executing-plans`、
`finishing-a-development-branch`、`receiving-code-review`、
`requesting-code-review`、`subagent-driven-development`、
`systematic-debugging`、`test-driven-development`、`using-git-worktrees`、
`using-superpowers`、`verification-before-completion`、`writing-plans`、
`writing-skills`

安装：

```bash
codex plugin add superpowers@openai-curated
```

### codex-security@openai-curated（12 个）

`attack-path-analysis`、`deep-security-scan`、`finding-discovery`、
`fix-finding`、`propose-security-hardening`、`security-diff-scan`、
`security-scan`、`threat-model`、`track-findings`、`triage-finding`、
`validation`、`vulnerability-writeup`

安装：

```bash
codex plugin add codex-security@openai-curated
```

### agentmemory@agentmemory 0.9.27（15 个）

`agentmemory-agents`、`agentmemory-architecture`、`agentmemory-config`、
`agentmemory-hooks`、`agentmemory-mcp-tools`、`agentmemory-rest-api`、
`commit-context`、`commit-history`、`forget`、`handoff`、`recall`、
`recap`、`remember`、`session-history`、`write-agentmemory-skill`

安装：

```bash
npm install -g @agentmemory/agentmemory@0.9.27
codex plugin marketplace add rohitg00/agentmemory
codex plugin add agentmemory@agentmemory
agentmemory connect codex --with-hooks
```

agentmemory 服务需要在独立终端运行：

```bash
agentmemory
```

### oh-my-codex@oh-my-codex-local 0.18.15（29 个）

`ai-slop-cleaner`、`analyze`、`ask`、`autopilot`、`autoresearch`、
`autoresearch-goal`、`best-practice-research`、`cancel`、`code-review`、
`configure-notifications`、`deep-interview`、`design`、`doctor`、`hud`、
`omx-setup`、`performance-goal`、`pipeline`、`plan`、
`prometheus-strict`、`ralph`、`ralplan`、`skill`、`team`、`ultragoal`、
`ultraqa`、`ultrawork`、`visual-ralph`、`wiki`、`worker`

安装：

```bash
npm install -g oh-my-codex@0.18.15
omx setup --scope user
omx doctor
```

## 四、本次会话可见、但不在本机 `codex plugin list` 已启用清单中的能力

这些能力可能来自会话连接器、工具源码或旧缓存。若另一台电脑也需要，应单独安装，不要直接复制缓存目录。

- GitHub：`gh-address-comments`、`gh-fix-ci`、`github`、`yeet`
- Gmail：`gmail`
- Understand Anything：`understand`、`understand-chat`、
  `understand-dashboard`、`understand-diff`、`understand-domain`、
  `understand-explain`、`understand-knowledge`、`understand-onboard`

GitHub 与 Gmail 可按需安装：

```bash
codex plugin add github@openai-curated
codex plugin add gmail@openai-curated
```

Understand Anything 官方 Codex 安装方式：

```bash
git clone https://github.com/Egonex-AI/Understand-Anything ~/.understand-anything/repo
bash ~/.understand-anything/repo/install.sh codex
```

执行第三方安装脚本前，建议先检查仓库和 `install.sh` 内容。

## 五、推荐安装顺序

```bash
# 1. 先安装并登录 Codex CLI，然后确认版本
codex --version

# 2. 解压本机独立 Skills
mkdir -p ~/.codex/skills
tar xzf codex_local_skills_20260810.tar.gz -C ~/.codex/skills

# 3. 安装 OpenAI 官方市场插件
codex plugin add superpowers@openai-curated
codex plugin add codex-security@openai-curated

# 4. 安装 OMX
npm install -g oh-my-codex@0.18.15
omx setup --scope user
omx doctor

# 5. 安装 agentmemory
npm install -g @agentmemory/agentmemory@0.9.27
codex plugin marketplace add rohitg00/agentmemory
codex plugin add agentmemory@agentmemory
agentmemory connect codex --with-hooks

# 6. 最后检查插件
codex plugin list
```

## 六、不要迁移的内容

不要整包复制 `~/.codex`。其中可能包含登录状态、令牌、会话记录、机器路径和缓存。

尤其不要复制：

- `~/.codex/auth.json` 或其他凭据文件
- `~/.codex/sessions/`
- `~/.codex/plugins/cache/`
- `~/.codex/.tmp/`
- 旧电脑的整个 `config.toml`（应只迁移确认过的非敏感配置项）

## 七、安装后验收

```bash
codex --version
node --version
npm --version
omx --version
omx doctor
agentmemory --version
codex plugin list
rg --hidden --files ~/.codex/skills -g 'SKILL.md' | sort
```
