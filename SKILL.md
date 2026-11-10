---
name: audio-runtime
description: 统一音频端点枚举、音频文件探测、单/双设备播放、Windows 多 Host API 原始 PCM 录音、以及转码/标准化到测试播放格式（44100 Hz 立体声 pcm_s16le）的 canonical 底层 Skill。设备按稳定 device_key 定位，播放与录音都产出带命令、字节数、北京时间戳的 JSON 回执；不捆绑任何二进制，不写凭据。
---

# audio-runtime

一个跨 Windows / Linux 的音频视图与音频执行层。它回答四个问题：

1. 这台机器上有哪些播放端点和采集端点，各自的稳定 `device_key` 是什么？
2. 这个音频文件是什么格式、多长、峰值和均值电平多少、是否削顶？
3. 把这段声音放到哪个（或哪两个）设备上，实际执行了什么命令？
4. 录下来的这一段是不是完整的、够长的、没被短读截断？

## 何时使用

- 测试用例需要"播放一段提示音，然后断言收到了什么"：用 `play` / `dual-play`，拿回执当真源，不要自己拼 `ffplay` 或 pygame。
- 用例需要录一段麦克风/前端音频：用 `record`，它调用 OmniPickup 并校验字节数，短读会报警。
- 用例产出了 `wav/pcm`，需要判断"到底有没有声音、有没有削顶"：用 `probe`，不要用文件大小猜。
- 需要把不同采样率的素材统一成测试播放格式：用 `normalize`。
- 新机器第一次跑音频用例：先 `doctor`，缺什么一目了然。

## 何时不要使用

- 需要合成语音（TTS）：用 `tts-runtime`，它产出音频后交给本仓库播放。
- 需要 USB/串口/板卡/驱动视图：用 `device-io`。
- 需要烧录固件、刷机、改节点配置：本仓库不碰硬件配置，只碰音频流。
- 需要图形界面：本仓库只有 CLI 与 JSON。

## 快速开始

```bash
pip install -e .

# 这台机器缺什么
audio-runtime doctor

# 枚举端点（Windows 走 PnP/OmniPickup，Linux 走 ALSA）
audio-runtime endpoints
audio-runtime endpoints --direction capture --format table

# 探测一个文件（原始 PCM 必须给出格式三元组）
audio-runtime probe take.wav
audio-runtime probe take.pcm --sample-rate 16000 --channels 8 --bit-depth 16

# 播放：系统默认端点必须显式 --force-default
audio-runtime play --device WASAPI:SPEAKERS-LISTENAI-AUDIO --force-default --audio-file tone.wav

# 双设备同步播放
audio-runtime dual-play \
  --left-device WASAPI:SPEAKERS-LISTENAI-AUDIO --left-file left.wav \
  --right-device "VID_2886&PID_0018" --right-file right.wav

# 录音：输出为无文件头原始 PCM
audio-runtime record --device "VID_2886&PID_0018" --output take.pcm \
  --sample-rate 16000 --channels 8 --bit-depth 16 --duration 5

# 转码 / 标准化
audio-runtime normalize take.pcm take.wav
```

## 三条硬规则

1. **默认端点不是默认目标。** `is_default` 为真的端点必须加 `--force-default` 才能播放；无人值守的智能体不能把测试室的音箱说响。
2. **设备用 key 定位，不用序号。** `--device` 接受 `device_key`、`endpoint_id` 或名称的唯一子串；匹配到多个就报错并列候选，绝不"取第一个"。
3. **回执是证据。** 每次播放/录音/转码都返回 JSON 回执（命令、字节数、`+08:00` 时间戳、warning）。用例归档回执，不要只记一句"播放成功"。

## 约定

- 时间戳一律 `2026-11-10T21:55:03.123+08:00`，无时区的时间会被 schema 拒绝。
- 原始 PCM 是**无文件头、交织、小端、有符号**；只知道字节数无法解释它，所以格式三元组必须一起记录。
- 录音字节数与理论值偏差超过 5 % 只报警不失败（真实掉帧需要人看），但退出码 0 且文件为空一律判失败。
- 不捆绑 ffmpeg / ffprobe / ffplay / OmniPickup；缺了就是 `backend-unavailable`（退出码 3），并给出安装提示。

完整契约（schema、退出码、`device_key` 推导规则、backend 解析顺序）见 `docs/interface.md`。

## 自检

```bash
tools/run-all.sh
```

单测 + CLI 契约 + 两份 fixture 回放 + WAV/裸 PCM 往返 + 播放/录音计划 + 转码 dry-run + 密钥扫描；最后打印 `ALL CHECKS PASSED`。
