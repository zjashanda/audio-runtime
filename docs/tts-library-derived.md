# TTS 音频库派生处理边界

- 适用版本：`audio-runtime` 0.1.1
- 更新时间：2026-11-12 17:06:15
- 关联：NOAH-66；规格 `tts-library-proposal.md` v1.0.0；`tts-runtime` 的 `docs/library-integration.md`

## 一句话

`audio-runtime` 只对**任务目录内的音频副本**做派生处理（重采样、裁剪、增益、降噪、格式转换与播放格式归一化）。1006 库里的共享原件始终不可变；派生结果记录来源、上游 sha256、有序处理链和输出 sha256，但**不回写库根、不发布、不删除、不读取库侧凭据**。

## 适用范围

- 本仓的派生职责覆盖：采样率/声道/编码转换（`convert`）、播放格式归一化（`normalize`）、处理前后的格式与完整性探测（`probe`）。
- 当前已发布的 CLI 只有 `convert` 与 `normalize` 两个写文件的派生命令；**裁剪、增益、降噪没有专用子命令**。需要用这些处理时，只能在任务副本上调用 `ffmpeg` 原语（或等本仓新增命令），并把完整 argv 与 ffmpeg 版本写入处理链。文档不得把尚不存在的参数写成 `audio-runtime` 的现有能力。
- 不负责：合成（属于 `tts-runtime`）、缓存键/命中判断/发布（属于 `asset-tools`）、播放与录音策略（本仓的 `play`/`record` 只服务测试场景，不参与库流程）。

## 三条不变式

1. **原件不可变**：库内资产发布后只读。任何处理只能作用于 `fetch` 到任务目录后的副本；不得原地修改、截断、重命名或改权限。
2. **副本必须真复制**：禁止硬链接、符号链接、共享可写挂载或 reflink 之外的任何「看起来像副本、实际共享 inode」的做法。`cp -l`、`cp -s`、`ln` 一律拒绝。
3. **来源可追溯**：每个派生文件都必须能回答四个问题：从哪个 `asset_id` 来、上游音频的 sha256 是什么、按什么顺序执行了哪些命令与参数、输出文件的 sha256 是什么。缺任一项即视为无来源产物，不得进入结果目录或回执。

## 前置：先 fetch 到任务目录

如果调用方手里只有库内路径（例如 `.../tts-library/tts-<key16>/<sha>.wav`），**不得直接处理该路径**。必须先通过 `asset-tools tts-library fetch` 把资产复制到任务目录：

```text
<task>/assets/tts/original/<asset_id>/<audio_sha256>.wav
```

- `fetch` 的参数、ACL 与 query-back 以 `asset-tools` 的 `tts-library` 契约为准，本仓不重复定义。
- `fetch` 落盘后应保持只读；派生处理前先复制到 `work/audio/` 或 `result/audio/` 并显式获得可写副本。
- 只有库路径、没有 `asset_id` 或 `audio.sha256` 时，不得开始处理：先补齐来源信息，否则产物无法追溯。

## 派生记录字段

每个派生文件建议随产物写一份 `derived.json`（本文只约定字段语义，不新增平台 schema）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `derived_from_asset_id` | 是 | 上游库资产 ID，如 `tts-0123456789abcdef` |
| `upstream_audio_sha256` | 是 | 上游 `manifest.audio.sha256`，必须与副本实测一致 |
| `processing_chain` | 是 | 有序数组；每步含 `seq`、`operation`、`tool`、`tool_version`、`command[]`、`params` |
| `output.sha256` | 是 | 输出文件实测 sha256（64 位小写十六进制） |
| `output.bytes` / `output.path` | 是 | 字节数与任务目录内相对路径 |
| `created_at` | 是 | 北京时间 `YYYY-MM-DD HH:MM:SS`，无时区后缀 |
| `warnings[]` | 否 | 探测/转换的非致命提示 |

注意：`audio-runtime convert` 的 `ark.audio-convert-receipt.v1` 只包含 `source`/`target`、格式参数、`command[]`、`exit_code`、`stderr_tail`、时间戳与 `warnings[]`，**没有 sha256 字段**。输出摘要必须由 `audio-runtime probe` 的 `ark.media-probe.v1.sha256`（或独立 `sha256sum`）实测后写入派生记录，不能从转换回执臆造。

## 工作示例

假设已命中资产 `tts-0123456789abcdef`，上游音频 sha256 为 `<64hex>`，任务根为 `/home/zhsh/workspace/tasks/NOAH-66`：

```bash
TASK_DIR=/home/zhsh/workspace/tasks/NOAH-66
ASSET_ID=tts-0123456789abcdef
UPSTREAM_SHA=<64hex>

# 1) 只复制到任务目录；不要直接处理库路径
mkdir -p "${TASK_DIR}/assets/tts/original/${ASSET_ID}"
asset-tools tts-library fetch   # 具体参数以 asset-tools 契约为准

# 2) 复制成可写的任务副本（禁止 -l / -s）
STAGE="${TASK_DIR}/work/audio/derived/${ASSET_ID}"
mkdir -p "${STAGE}"
cp "${TASK_DIR}/assets/tts/original/${ASSET_ID}/${UPSTREAM_SHA}.wav" \
   "${STAGE}/input.wav"
chmod u+w "${STAGE}/input.wav"

# 3) 记录上游事实并核对 sha256
audio-runtime probe "${STAGE}/input.wav" --format json \
  > "${STAGE}/probe-upstream.json"
sha256sum "${STAGE}/input.wav"

# 4) 派生处理：归一化到播放格式，回执保留真实 argv
audio-runtime normalize "${STAGE}/input.wav" "${STAGE}/${ASSET_ID}.bench.wav" \
  --format json > "${STAGE}/convert-receipt.json"

# 5) 实测输出摘要（转换回执没有 sha256）
audio-runtime probe "${STAGE}/${ASSET_ID}.bench.wav" --format json \
  > "${STAGE}/probe-output.json"

# 6) 工具版本写进 processing_chain
audio-runtime --version
```

对应的派生记录示例：

```json
{
  "derived_from_asset_id": "tts-0123456789abcdef",
  "upstream_audio_sha256": "<64hex>",
  "processing_chain": [
    {
      "seq": 1,
      "operation": "normalize",
      "tool": "audio-runtime",
      "tool_version": "0.1.1",
      "command": [
        "ffmpeg", "-y", "-v", "error", "-i", "input.wav", "-vn",
        "-acodec", "pcm_s16le", "-ac", "2", "-ar", "44100",
        "tts-0123456789abcdef.bench.wav"
      ],
      "params": {"sample_rate": 44100, "channels": 2, "codec": "pcm_s16le"}
    }
  ],
  "output": {
    "path": "work/audio/derived/tts-0123456789abcdef/tts-0123456789abcdef.bench.wav",
    "sha256": "<64hex>",
    "bytes": 123456
  },
  "created_at": "2026-11-12 17:06:15"
}
```

如果还需要裁剪、增益或降噪，继续在同一副本上追加处理步骤，例如用 ffmpeg 的 `atrim`/`volume`/`afftdn` 滤镜；每一步都要把完整命令与参数追加到 `processing_chain`，并在最后重新 `probe` 取输出 sha256。不得省略中间步骤，也不得把多个处理合并成一条无法复现的「黑盒」记录。

## 拒绝矩阵

| # | 场景 | 判定 | 处理 |
|---|---|---|---|
| 1 | 直接对库内原文件执行 `normalize`/`convert`/ffmpeg | **拒绝** | 先 `fetch` + 复制；库根只读 |
| 2 | 用硬链接（`cp -l`、`ln`）把库文件「复制」到任务目录 | **拒绝** | 必须真复制；共享 inode 会污染原件 |
| 3 | 用符号链接或共享可写挂载指向库根 | **拒绝** | 落盘为任务目录内独立文件 |
| 4 | 派生记录缺少 `derived_from_asset_id`、上游 sha256 或输出 sha256 | **拒绝登记** | 补齐四个来源字段后才能进结果目录 |
| 5 | 把派生结果写回库根、覆盖原资产或提交 `publish` | **拒绝** | 本仓不发布；派生资产入库需单独审批 |
| 6 | 只拿到库路径就本地处理，未先 `fetch` | **拒绝** | 先复制到 `<task>/assets/tts/original/<asset_id>/` |
| 7 | 为了让 `fetch`/`publish` 成功而读取或传递凭据 | **拒绝** | 本仓不读凭据；上传凭据由 `asset-tools`/环境变量负责 |
| 8 | 修改原件的权限、mtime、文件名或内容 | **拒绝** | 原件保持只读；只改任务副本 |
| 9 | 处理链只写「已降噪」，没有命令、参数、工具版本 | **拒绝登记** | 处理链必须可复现 |
| 10 | 输出为空文件、不可读或 sha256 与记录不符 | **拒绝** | 重新执行并核对；空产物不得当成功 |

## 退出码与处置

| 退出码 | 含义 | 处置 |
|---|---|---|
| 0 | 成功 | 记录回执与输出 sha256，进入下一步 |
| 2 | 用法错误（参数/文件缺失、默认端点未加 `--force-default`） | 修正参数；这不是音频损坏 |
| 3 | 后端不可用（ffmpeg/ffprobe/播放器缺失） | 安装工具后重试；不得跳过转换继续 |
| 6 | 探测失败（不可读、坏 WAV 头、无法判断格式） | 停止；不得用坏文件派生 |
| 7 | 转换失败（ffmpeg 非零或无输出） | 保留 `stderr_tail`，修复后重跑；不得发布半成品 |
| 130 | 操作员中断 | 清理本任务半成品，重新执行 |

## 与相邻仓库的分工

- `tts-runtime`：只做 cache miss 合成，产出原始 wav；不做任何后处理。
- `asset-tools`：缓存键、`lookup`/`fetch`/`publish`/`verify`/`index`、锁、ACL、outbox、query-back；库根唯一写入方。
- `audio-runtime`：只处理任务副本，输出可复核的转换回执与派生记录；不判断命中，不参与发布。
- `ark-kb`：只存可搜索索引，不存音频本体。

## 明确不做

- 不写 `/srv/storage/ark/artifacts/audio/tts-library`，不删除、不移动、不修复库内文件。
- 不为派生资产计算缓存键，不决定是否复用，不发布派生结果。
- 不读取、保存或转发任何库侧凭据；`node-storage` 凭据由 `asset-tools`/环境变量管理。
- 不把「播放格式归一化」误写成「库格式」：`normalize` 的目标是 44100Hz / stereo / `pcm_s16le` 的测试播放格式，不是 1006 资产的 canonical 格式。
- 不在系统临时目录保存正式产物；所有副本、回执与派生文件都在任务目录内。

## 相关文档

- `asset-tools`：`docs/tts-library.md`（库布局、fetch/publish、ACL、outbox 的唯一权威）
- `tts-runtime`：`docs/library-integration.md`（只在 cache miss 合成、结果契约与退出码）
- 本仓：`docs/interface.md`（`convert`/`normalize`/`probe` 的公开契约与退出码）、`SKILL.md`
