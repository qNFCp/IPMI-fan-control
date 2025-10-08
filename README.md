# IPMI-fan-control
Use IPMI to control server fan speed | 使用IPMI控制服务器风扇转速

## 功能
+ 使用IPMI控制风扇速率
+ 采集温度，根据不同温度，调整不同风扇速率（采样为所有温度传感器中的最高值。通常为CPU温度）
+ 间隔采样，每隔 X 秒采样并调整一次速率
+ 夜间封顶，可设置夜间时段，及夜间时段最大风扇转速

## 脚本兼容性(已知)
| 系统          | 支持情况    |
| ------------- | ----------- |
| Ubuntu 24.04  | √           |

| 服务器              | 支持情况    |
| ------------------- | ----------- |
| Dell R540 (iDRAC9） | √           |

## 依赖安装
### Ubuntu
```shell
sudo apt update

sudo apt install ipmitool

sudo apt install python3-apscheduler
```

## 运行
### 测试运行
```shell
python3 main.py
```

### 后台运行
> 如果需要开机自启(设为服务)，就不要运行这个
```shell
nohup python3 main.py & 


```

### 创建为服务 (开机自启)
#### Ubuntu
1. 创建服务文件
```shell
sudo touch /etc/systemd/system/ipmi-fan.service
```

2. 配置服务文件
```shell
sudo nano /etc/systemd/system/ipmi-fan.service
```

3. 将以下内容写入文件
> (需修改路径配置)
```service
[Unit]
Description=IPMI Fan Auto Control
After=network.target

[Service]
Type=simple
WorkingDirectory=程序目录绝对路径如/home/user/ipmi
ExecStart=/usr/bin/python3 py绝对路径,如/home/user/ipmi/main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

4. 开启服务，及开启开机自启
```shell
sudo systemctl daemon-reload
sudo systemctl enable --now ipmi-fan.service
```
