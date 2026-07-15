# BACnet-HA

通过 BACnet 网关将 BACnet 设备接入 Home Assistant 的定制集成。

## 概述

BACnet-HA 是一个 Home Assistant 自定义集成，用于通过**讯饶 Router1001-ARM-E**（或其他 BACnet IP↔MS/TP 路由器）连接 BACnet MS/TP 设备。它替代了 BBMD/Foreign Device 架构——由网关处理跨子网路由，HA 只需直连网关 IP。

### 核心特性

- **跨子网直连** — 讯饶网关自动路由 MS/TP ↔ IP，无需 BBMD
- **实时更新** — COV（Change of Value）事件驱动 + 轮询双重保障
- **故障恢复** — 自动重连 + HA 原生退避策略
- **灵活对象映射** — 每个 BACnet 对象可独立配置 HA 领域（sensor/number/switch/climate/select）
- **写优先数组** — BACnet Priority Array 写入 + Null 释放
- **预设 Honeywell FT-82** — 5 个对象开箱即用

### 网络拓扑

```
HA 服务器 ──BACnet/IP──► 讯饶 Router1001-ARM-E (192.168.100.103:47808)
                                 │
                                 └──BACnet MS/TP──► Honeywell HT9612D3100
                                                           │
                                                     ┌─────┴─────┐
                                                     │ AC / 风机 │
```

## 安装

### 通过 HACS 安装（推荐）

1. 打开 HACS → 自定义仓库 → 添加仓库 URL
2. 仓库：`https://github.com/z-h/BACnet-HA`，类型：`Integration`
3. 搜索并安装 "BACnet-HA"
4. 重启 Home Assistant

### 手动安装

将 `custom_components/bacnet_ha/` 目录复制到 Home Assistant 的 `custom_components/` 目录：

```bash
cp -r custom_components/bacnet_ha /path/to/ha/config/custom_components/
```

重启 Home Assistant。

## 配置

### 通过 UI 配置（推荐）

1. **设置 → 设备与服务 → 添加集成** → 搜索 "BACnet-HA"
2. 点击 **BACnet-HA** → 自动扫描网络中的 BACnet 设备
3. 选择发现的设备（或手动输入网关配置）
4. 选择要导入的 BACnet 对象（预设 Honeywell FT-82）
5. 完成

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `local_ip` | 自动检测 | HA 服务器本机 IP |
| `local_port` | 47808 | 本地 BACnet/UDP 端口 |
| `gateway_ip` | _用户输入_ | 讯饶 Router1001-ARM-E 的 IP |
| `gateway_port` | 47808 | 网关 BACnet/IP 端口 |
| `device_id` | 4 | 目标 BACnet 设备实例 ID |
| `polling_interval` | 30s | COV 不可用时的轮询间隔 |
| `enable_cov` | true | 是否启用 COV 订阅 |
| `write_priority` | 16 | BACnet Priority Array 写入优先级 |

## 预设对象映射

该集成预置了 **Honeywell FT-82** 温控器的 5 个标准 BACnet 对象：

| 实例 | 名称 | BACnet类型 | HA领域 | 读写 |
|------|------|-----------|--------|------|
| 24 | RoomTemperature | AnalogValue | `sensor` | 只读 |
| 25 | RoomSetpoint | AnalogValue | `climate` | 读写 |
| 26 | FanSwitch | AnalogValue | `number` | 读写 |
| 27 | SystemSwitch | AnalogValue | `number` | 读写 |
| 28 | PowerSwitch | AnalogValue | `number` | 读写 |

所有对象可在集成选项（Options）中自由切换 HA 领域映射。

## 开发

```bash
# 克隆
git clone https://github.com/z-h/BACnet-HA.git
cd BACnet-HA

# 依赖
pip install -r requirements.txt
# 主要依赖: bacpypes3>=2.2.0

# 代码结构
custom_components/bacnet_ha/
├── __init__.py          # 入口 + 预设对象 + 数据归一化
├── bacnet_client.py     # BACpypes3 封装
├── coordinator.py       # COV + 轮询协调器
├── config_flow.py       # 3步配置流
├── const.py             # 常量 + FT-82 预设
├── entity.py            # 基类实体
├── sensor.py            # sensor 平台
├── number.py            # number 平台
├── switch.py            # switch 平台
├── binary_sensor.py     # binary_sensor 平台
├── select.py            # select 平台
├── climate.py           # climate 平台
├── helpers.py           # 工具函数
├── manifest.json        # 集成声明
├── strings.json         # UI 文本
└── translations/        # 翻译
```

## 许可

Apache License 2.0

## 鸣谢

- [CervezaStallone BACnet](https://github.com/cervezastallone/bacnet-ha) — 原始 BACnet 集成（本 fork 的源项目）
- 讯饶科技 — Router1001-ARM-E BACnet 网关
- Honeywell — HT9612D3100 Fan Coil Thermostat Driver