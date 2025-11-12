# Ps-Comfy-TiHeaveN-CustomNodes 说明文档

# 项目介绍

Ps-Comfy-TiHeaveN-CustomNodes（ComfyUI 节点）需配合 Ps-Comfy-TiHeaveN-UXP-Plugin（PS 插件）一起使用，主要功能为在Photoshop中进行局部重绘。通过 PS 插件与 ComfyUI 联动，实现选区自动扩展边界以提升参考度，同时支持采样器实时预览、智能对象返回及蒙版生成。<br>
![](images/2025-11-12-20-26-44.png)

---

![](images/2025-11-12-20-27-59.png)<br>
---
- **ComfyUI 节点**：安装后并无可视化节点，仅为 Comfy服务器 新增通信路由；

- **PS 插件**：一个迷你版的ComfyUI

- **核心流程**：PS 勾选选区 → 传参至 ComfyUI → 实时预览 → 智能对象原位贴回（带蒙版）。

- **原理**：PS插件访问Comfy服务器的相关路由，获取改造的工作流，发回服务器运行，PS插件不会对原工作流文件进行任何修改及保存，不用打开浏览器，可在ComfyUI的控制台查看详细进程。ComfyUI\script_examples下有相关示例

- **亮点**：无需选中指定图层，也不用提前创建蒙版，选区对应的内容就是当前可见的图像内容。图像将以无损 PNG(RGBA) 格式传输至服务器，不会降低画质，最终将扩展边界后的矩形图像通过 “加载图像节点” 加载，再把选区作为遮罩传入了该节点。<br>
- *注意: 传输的临时图像在此文件夹`ComfyUI\input\Ps-Comfy-TiHeaveN`，请定期清理。*

- **其它小功能**：中止当前队列、中止全部队列、释放模型及缓存占用、队列实时状态、内存占用情况。<br><br>
队列实时状态：当你同时打开了PS插件和浏览器，浏览器中正在进行生图，此时PS插件中会显示队列的信息，包括排队状态(当你在PS中执行了队列)。<br><br>
![](images/2025-11-12-22-39-52.png)<br>![](images/2025-11-12-22-42-05.png)<br>![](images/2025-11-12-22-44-08.png)<br>

# 依赖环境

- **Photoshop**：>= 26.0.0

- **ComfyUI**：推荐最新版本，低版本需自行测试兼容性。

# 安装教程

## 1. 安装 PS 插件 

1. 下载 PS 插件 Ps-Comfy-TiHeaveN-UXP-Plugin
  - [Github](https://github.com/tiheaven/Ps-Comfy-TiHeaveN-CustomNodes/releases/download/v1.0.3/Ps-Comfy-TiHeaveN-UXP-Plugin.1.0.3.zip)
  - [百度网盘](https://pan.baidu.com/s/51RoomjsOFPTjjSyONh5i_A)
  - [夸克网盘](https://pan.quark.cn/s/5ffdb70ca4e3)

2. 解压至 Photoshop 插件目录（例：Adobe Photoshop 2025\Plug-ins）；

3. 确认解压后目录内可直接看到 manifest.json 文件；<br>
![](images/2025-11-12-23-13-20.png)<br>

4. 进入 Photoshop ，打开 增效工具 菜单 即可看到此插件。

## 2. 安装 ComfyUI 节点

1. 在 ComfyUI 自定义节点目录下：ComfyUI\custom_nodes\；

2. 克隆本仓库：
        `git clone https://github.com/tiheaven/Ps-Comfy-TiHeaveN-CustomNodes.git`

3. 确认目录结构为 ComfyUI\custom_nodes\Ps-Comfy-TiHeaveN-CustomNodes，即安装成功。

# 工作流设置

## 1. 工作流专属文件夹配置

1. 创建主目录：ComfyUI\user\default\workflows\Ps-Comfy-TiHeaveN\；

2. 在主目录下创建子目录，命名规则为 01 目录名（插件中按数字排序，仅显示 “目录名”）；

3. 在子目录中放置工作流 JSON 文件，命名规则为 01 工作流名称 [描述]（数字用于排序，插件中显示 “工作流名称”）。<br>
![](images/2025-11-12-22-51-06.png)<br>

## 2. 现有工作流改造规则

- **流程起点与终点**：以 “加载图像” 为起点，“预览图像” 为终点（可参考示例工作流）；

- **节点暴露规则**：
        节点命名格式：#01 节点名 [参数1,参数2]（例：#01 K采样器 [seed,denoise]），插件仅显示指定参数（如随机种、降噪值）；<br>
        ![](images/2025-11-12-22-53-38.png)

- 若不指定参数（仅 #01 节点名），插件将显示整个节点的所有参数；

- 建议：提前在 ComfyUI 中调试好工作流，仅暴露关键参数至插件；

**冲突提示**：aigodlike-comfyui-translation 翻译节点可能还原节点命名，导致上述规则失效，需注意。

# 多语言支持

- **语言切换**：在插件设置中可切换语言，内置：简体中文（默认）、繁体中文、英文；

- **语言包路径**：ComfyUI\custom_nodes\Ps-Comfy-TiHeaveN-CustomNodes\locales\；

- **新增语言**：复制 en_US.json 并重命名为 <语言代号>.json（如 de_DE.json），通过 AI 翻译内容即可（本插件的繁体中文和英文语言包均由豆包翻译生成）。

# 其他说明

## 启动验证

启动 ComfyUI 后，若控制台输出以下内容，说明 Ps-Comfy-TiHeaveN-CustomNodes 加载成功：

```plaintext

[Ps-Comfy-TiHeaveN]: If you see me, it means the loading has been successfully completed.
```

## 新增路由

插件运行依赖以下路由，需确保可访问：

- `http://127.0.0.1:8188/workflows/`：
用于获取 `ComfyUI\user\default\workflows\Ps-Comfy-TiHeaveN\` 下的工作流文件；

- `http://127.0.0.1:8188/ps-comfy-tiheaven-locales/`：
用于获取语言包。

若在云端使用，需确保上述两个路由可正常访问。

## 关于初始版本

初始设计了可进行多队列，实测有BUG，且对于整个流程无意义，于是去除了多队列功能。

## 反馈渠道

如有问题或建议，可前往：[https://space.bilibili.com/399703773](https://space.bilibili.com/399703773) 反馈，第一次用Github很多东西不熟。