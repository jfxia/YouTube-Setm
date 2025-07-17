# YouTube-Setm
整合了YouTube-Downloader(github.com/jfxia/YouTube-Downloader)与Setm(github.com/jfxia/Setm)的功能，实现YouTube视频处理一条龙：从YouTube下载英文/日文视频-->语音字幕提取为SRT文件-->字幕文件翻译为中文-->合成新的中文视频文件。若选择音频模式，则仅下载为MP3文件

## 程序依赖

**-- Python 3.8及以上版本**

**-- yt-dlp** （安装后在PATH环境变量中设定）

**-- OpenAI Whisper** (github.com/openai/whisper，模型建议选择small）)

**-- DeepSeek API Key** (订阅platform.deepseek.com，并将API Key写在配置文件config.ini中)

**-- FFMpeg**（安装后在PATH环境变量中设定）

## 用法

```
python .\youtube-setm.py
```

## 界面截屏

![截屏](/assets/screenshot1.png)

![截屏](/assets/screenshot2.png)

![截屏](/assets/screenshot3.png)

![截屏](/assets/screenshot4.png)
