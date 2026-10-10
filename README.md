# 串口数据包工具

基于 PySide6 和 Qt Serial Port 的桌面串口工具。启动时递归扫描进程当前工作目录内的 JSON 文件，并将单包定义和包序列分别显示。中栏从上到下依次为包列表、包序列列表、当前发送结构。打开和保存对话框默认使用该目录。默认串口参数为 `115200 8N1`，支持字段编辑、单次发送、按 Hz 循环发送和十六进制收发记录。`Ctrl+S` 保存当前单包或序列，`Ctrl+O` 打开 JSON。

代码按职责拆分：`main.py` 创建应用并加载窗口，`ui.py` 实现界面和交互，`serial.py` 管理串口收发与帧解码，`protocol.py` 负责 JSON 数据包读写、定义校验、编码和解码。

点击“新建序列”并输入名称可创建序列；在包列表多选一个或多个包后点击 `+`，可将它们加入当前序列。底部 `−` 可移除选中的序列成员。点击当前发送结构中的任意包，可在右侧字段树查看和编辑它的变量；“保存当前单包”将修改保存到单包 JSON，`Ctrl+S` 保存整个序列。序列按列表顺序拼接预览和发送。

单包 JSON 只保存一个包的定义。导出序列 JSON 时会把当前序列中的每个包及其变量值完整保存为快照，打开时无需依赖原包文件；加载器也兼容旧版只写包名的序列 JSON，例如 `"packet_sequence": ["frame_header_t", "ext_robot_interaction_data_t"]`。

## 运行

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 数据包 JSON 格式

每个单包 JSON 使用顶层 `packet` 保存唯一包定义。`type` 是 C 字段类型，`name` 是字段名，`comment` 是可选中文注释，普通字段使用 `value`；结构体字段使用 `kind: "struct"` 和 `fields`；标量定长数组使用 `kind: "array"`、元素 `type` 和 `values`。结构体数组使用相同的 `kind: "array"`，并用 `items` 列出结构体实例；`fields` 可继续包含结构体或数组，因此支持任意层级的结构体嵌套。界面按 C 类型、变量名、中文注释、数值四列显示；双击中文注释单元格可编辑。支持 `int8_t`、`uint8_t`、`int16_t`、`uint16_t`、`int32_t`、`uint32_t`、`int64_t`、`uint64_t`、`float`、`double`。
整数变量可以在界面或 JSON 中使用十六进制值；JSON 中需将其写为字符串，例如 `"value": "0x0301"`。打包时会按字段的 C 类型进行范围校验和小端编码。

结构体数组的 JSON 形式示例：

```json
{
	"kind": "array",
	"type": "SensorChannel",
	"name": "channels",
	"items": [
		{
			"kind": "struct",
			"type": "SensorChannel",
			"name": "channel_0",
			"fields": [
				{"type": "uint8_t", "name": "id", "comment": "通道编号", "value": 0},
				{"kind": "struct", "type": "Calibration", "name": "calibration", "fields": []}
			]
		}
	]
}
```

JSON 顶层的 `byte_order` 和 `packing` 分别固定为 `little` 和 `1`。编码按字段顺序直接拼接，不插入对齐填充；C 端应使用相同字段宽度、顺序和小端字节序，并通过 `#pragma pack(push, 1)` / `#pragma pack(pop)` 或编译器对应的 packed 属性定义结构体。

整数字段可用 `size()` 表达式自动填写指定数据包的编码字节数，例如帧头的长度字段可写为 `"value": {"dynamic": "size(ext_robot_interaction_data_t) + size(sentry_decision_data_t) + size(frame_tail_t)"}`。表达式支持一个或多个 `size(包名)` 项并以 `+` 相加；包名必须能在数据包目录或当前序列中找到定义，大小按字段宽度、嵌套结构和数组长度计算，不依赖序列中的包顺序。UI 会显示表达式及计算出的字节数。

CRC 表达式只能写在包序列 JSON 快照的整数变量中，不写入单包定义。格式为 `crc(算法,起始包序列位置,起始变量名,终止包序列位置,终止变量名)`，包位置从 1 开始，变量名支持结构体点路径。CRC 对起止变量之间连续的编码字节计算，包含两个端点；跨包范围也按序列顺序拼接。例如 `"value": {"dynamic": "crc(8,1,SOF,1,seq)"}` 对第 1 个包从 `SOF` 到 `seq`（含首尾）的字节计算 CRC8。`radar_to_sentry` 序列末尾追加 `frame_tail_t`，其 CRC16 覆盖从帧头 `SOF` 到数据包最后一个字段的全部字节，不包括 CRC16 本身。使用 RoboMaster CRC8（初值 `0xFF`、反射多项式 `0x8C`）和 CRC16（初值 `0xFFFF`、反射多项式 `0x8408`）。