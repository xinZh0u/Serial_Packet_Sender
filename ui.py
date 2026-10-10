import struct

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from protocol import (
    INTEGER_TYPES,
    document_packet_definitions,
    load_packet_file,
    load_packets_from_directory,
    pack_fields,
    pack_packet_sequence,
    packet_sequence_size_values,
    parse_crc_expression,
    parse_size_expression,
    read_packet_document,
    save_packet_document,
)
from serial import SequenceFrameDecoder, SerialController


class SerialPacketWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("串口数据包工具")
        self.resize(1180, 760)
        self.app_directory = Path.cwd()
        discovered_packets, self.packet_compositions, skipped_json_files = load_packets_from_directory(
            self.app_directory
        )
        self.packets = discovered_packets
        self.packet_catalog = {packet["name"]: packet for packet in self.packets}
        self.skipped_json_files = skipped_json_files
        self.serial_controller = SerialController(self)
        self.serial_controller.data_received.connect(self.handle_serial_data)
        self.serial_controller.error_occurred.connect(self.handle_serial_error)
        self.frame_decoder = SequenceFrameDecoder()
        self.current_packet: dict[str, Any] = self.packets[0]
        self.packet_sequence: list[dict[str, Any]] = []
        self.sequence_mode = False
        self.current_sequence_name: str | None = None
        self.current_sequence_path: Path | None = None
        self.editing_sequence_index: int | None = None
        self.current_size_values: dict[str, int] = {}
        self.tx_timer = QTimer(self)
        self.tx_timer.timeout.connect(self.send_current_packet)
        self._building_tree = False
        self.save_action = QAction("保存数据包", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_packet)
        self.addAction(self.save_action)
        self.open_action = QAction("打开数据包", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_packet)
        self.addAction(self.open_action)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 10, 12, 12)
        root_layout.setSpacing(10)
        root_layout.addWidget(self.make_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.make_serial_panel())
        splitter.addWidget(self.make_packet_panel())
        splitter.addWidget(self.make_editor_panel())
        splitter.setSizes([240, 235, 705])
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        status = (
            f"已加载 {len(self.packets)} 个数据包和 {len(self.packet_compositions)} 个组合包"
            " · 小端序 · 无自动填充"
        )
        if self.skipped_json_files:
            status += f" · 跳过 {len(self.skipped_json_files)} 个非数据包 JSON"
        self.statusBar().showMessage(status)
        self.refresh_ports()
        self.packet_list.setCurrentItem(self.packet_list.topLevelItem(0))

    def make_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        title = QLabel("PACKET SENDER")
        title.setObjectName("brand")
        layout.addWidget(title)
        layout.addSpacing(12)
        self.save_button = QPushButton("保存数据包")
        self.save_button.clicked.connect(self.save_packet)
        layout.addWidget(self.save_button)
        self.save_current_packet_button = QPushButton("保存当前单包")
        self.save_current_packet_button.clicked.connect(self.save_current_packet)
        layout.addWidget(self.save_current_packet_button)
        self.open_button = QPushButton("打开数据包")
        self.open_button.clicked.connect(self.open_packet)
        layout.addWidget(self.open_button)
        layout.addStretch(1)
        layout.addWidget(QLabel("发送频率"))
        self.frequency = QDoubleSpinBox()
        self.frequency.setRange(0.1, 1000.0)
        self.frequency.setDecimals(1)
        self.frequency.setValue(10.0)
        self.frequency.setSuffix(" Hz")
        self.frequency.setFixedWidth(120)
        self.frequency.valueChanged.connect(self.update_transmit_rate)
        layout.addWidget(self.frequency)
        self.send_once_button = QPushButton("发送一次")
        self.send_once_button.clicked.connect(self.send_current_packet)
        layout.addWidget(self.send_once_button)
        self.transmit_button = QPushButton("开始循环发送")
        self.transmit_button.setCheckable(True)
        self.transmit_button.toggled.connect(self.toggle_transmit)
        layout.addWidget(self.transmit_button)
        return bar

    def make_serial_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        self.settings_toggle = QToolButton()
        self.settings_toggle.setText("串口设置  ▾")
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(True)
        self.settings_toggle.toggled.connect(self.toggle_settings)
        layout.addWidget(self.settings_toggle)

        self.settings_content = QWidget()
        form = QVBoxLayout(self.settings_content)
        form.setContentsMargins(4, 2, 4, 4)
        form.setSpacing(6)
        form.addWidget(QLabel("串口"))
        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.port_combo.setMinimumContentsLength(10)
        port_row.addWidget(self.port_combo, 1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_ports)
        port_row.addWidget(self.refresh_button)
        form.addLayout(port_row)

        form.addWidget(QLabel("波特率"))
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"])
        self.baud_combo.setCurrentText("115200")
        form.addWidget(self.baud_combo)

        form.addWidget(QLabel("数据位 · 校验位 · 停止位"))
        line_row = QHBoxLayout()
        self.data_bits = QComboBox()
        self.data_bits.addItems(["5", "6", "7", "8"])
        self.data_bits.setCurrentText("8")
        self.parity = QComboBox()
        self.parity.addItem("无校验", "N")
        self.parity.addItem("偶校验", "E")
        self.parity.addItem("奇校验", "O")
        self.stop_bits = QComboBox()
        self.stop_bits.addItems(["1", "2"])
        line_row.addWidget(self.data_bits)
        line_row.addWidget(self.parity)
        line_row.addWidget(self.stop_bits)
        form.addLayout(line_row)

        self.connect_button = QPushButton("打开串口")
        self.connect_button.setObjectName("connectButton")
        self.connect_button.clicked.connect(self.toggle_serial)
        form.addWidget(self.connect_button)
        form.addStretch(1)
        layout.addWidget(self.settings_content)
        layout.addStretch(1)
        return panel

    def make_packet_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel("数据包目录"))
        self.packet_list = QTreeWidget()
        self.packet_list.setHeaderHidden(True)
        self.packet_list.setRootIsDecorated(False)
        self.packet_list.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.packet_list.currentItemChanged.connect(self.packet_selected)
        self.packet_list.itemClicked.connect(self.packet_clicked)
        self.refresh_packet_catalog()
        layout.addWidget(self.packet_list, 2)
        self.packet_description = QLabel()
        self.packet_description.setWordWrap(True)
        self.packet_description.setObjectName("muted")
        layout.addWidget(self.packet_description)

        layout.addWidget(QLabel("包序列 JSON"))
        sequence_toolbar = QHBoxLayout()
        sequence_toolbar.addStretch(1)
        self.create_sequence_button = QPushButton("新建序列")
        self.create_sequence_button.clicked.connect(self.create_packet_sequence)
        sequence_toolbar.addWidget(self.create_sequence_button)
        layout.addLayout(sequence_toolbar)
        self.sequence_catalog_list = QTreeWidget()
        self.sequence_catalog_list.setHeaderHidden(True)
        self.sequence_catalog_list.setRootIsDecorated(False)
        self.sequence_catalog_list.currentItemChanged.connect(self.packet_sequence_selected)
        self.sequence_catalog_list.itemClicked.connect(self.packet_sequence_clicked)
        self.refresh_sequence_catalog()
        layout.addWidget(self.sequence_catalog_list, 1)

        layout.addWidget(QLabel("当前发送结构"))
        self.send_structure_list = QTreeWidget()
        self.send_structure_list.setHeaderHidden(True)
        self.send_structure_list.setRootIsDecorated(False)
        self.send_structure_list.currentItemChanged.connect(self.send_structure_selected)
        layout.addWidget(self.send_structure_list, 1)
        sequence_controls = QHBoxLayout()
        sequence_controls.addStretch(1)
        self.add_sequence_button = QToolButton()
        self.add_sequence_button.setText("+")
        self.add_sequence_button.setToolTip("将包目录中选中的包添加到当前序列")
        self.add_sequence_button.clicked.connect(self.add_packet_to_sequence)
        sequence_controls.addWidget(self.add_sequence_button)
        self.remove_sequence_button = QToolButton()
        self.remove_sequence_button.setText("−")
        self.remove_sequence_button.setToolTip("从当前发送结构移除选中的包")
        self.remove_sequence_button.clicked.connect(self.remove_packet_from_sequence)
        sequence_controls.addWidget(self.remove_sequence_button)
        layout.addLayout(sequence_controls)
        return panel

    def make_editor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel("数据包字段"))

        self.field_tree = QTreeWidget()
        self.field_tree.setColumnCount(4)
        self.field_tree.setHeaderLabels(["C 类型", "变量名", "中文注释", "数值（支持 0x）"])
        self.field_tree.headerItem().setToolTip(3, "整数可直接输入十六进制，例如 0x0301")
        self.field_tree.setColumnWidth(0, 110)
        self.field_tree.setColumnWidth(1, 145)
        self.field_tree.setColumnWidth(2, 220)
        self.field_tree.setColumnWidth(3, 140)
        self.field_tree.itemChanged.connect(self.field_edited)
        layout.addWidget(self.field_tree, 3)

        self.hex_preview = QTextEdit()
        self.hex_preview.setReadOnly(True)
        self.hex_preview.setMaximumHeight(78)
        self.hex_preview.setPlaceholderText("编码后的十六进制数据")
        layout.addWidget(self.hex_preview)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("串口收发记录"))
        log_header.addStretch(1)
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(lambda: self.serial_log.clear())
        log_header.addWidget(clear_button)
        layout.addLayout(log_header)
        self.serial_log = QTextEdit()
        self.serial_log.setReadOnly(True)
        self.serial_log.setMaximumHeight(150)
        self.serial_log.setPlaceholderText("打开串口后，收发数据会显示在这里（十六进制）")
        layout.addWidget(self.serial_log, 2)
        return panel

    def toggle_settings(self, expanded: bool) -> None:
        self.settings_content.setVisible(expanded)
        self.settings_toggle.setText("串口设置  ▾" if expanded else "串口设置  ▸")

    def refresh_ports(self) -> None:
        selected = self.port_combo.currentData()
        self.port_combo.clear()
        for port in QSerialPortInfo.availablePorts():
            label = port.portName()
            if port.description():
                label += f" · {port.description()}"
            self.port_combo.addItem(label, port.portName())
        if selected:
            index = self.port_combo.findData(selected)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
        if self.port_combo.count() == 0:
            self.port_combo.addItem("未发现串口", "")

    def toggle_serial(self) -> None:
        if self.serial_controller.is_open():
            self.tx_timer.stop()
            self.transmit_button.setChecked(False)
            self.serial_controller.close()
            self.connect_button.setText("打开串口")
            self.statusBar().showMessage("串口已关闭")
            return

        port_name = self.port_combo.currentData()
        try:
            baud_rate = int(self.baud_combo.currentText())
        except ValueError:
            QMessageBox.warning(self, "参数错误", "波特率必须是整数。")
            return
        if not port_name:
            QMessageBox.warning(self, "未选择串口", "请先连接设备并刷新串口列表。")
            return

        try:
            opened = self.serial_controller.open(
                port_name,
                baud_rate,
                self.data_bits.currentText(),
                self.parity.currentData(),
                self.stop_bits.currentText(),
            )
        except (KeyError, ValueError) as error:
            QMessageBox.warning(self, "参数错误", f"串口参数无效：{error}")
            return
        if not opened:
            QMessageBox.critical(
                self, "串口打开失败", self.serial_controller.error_string()
            )
            return

        self.connect_button.setText("关闭串口")
        self.statusBar().showMessage(f"已连接 {port_name} · {baud_rate} baud")
        self.append_log("INFO", f"已连接 {port_name} · {baud_rate} baud")

    def packet_selected(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is not None:
            index = current.data(0, Qt.ItemDataRole.UserRole + 1)
            self.sequence_mode = False
            self.packet_sequence.clear()
            self.current_sequence_name = None
            self.current_sequence_path = None
            self.select_packet(index)
            self.rebuild_sequence_list()

    def packet_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        self.packet_selected(item, None)

    def refresh_packet_catalog(self, selected_entry: tuple[str, int] | None = None) -> None:
        self.packet_list.clear()
        for index, packet in enumerate(self.packets):
            item = QTreeWidgetItem([packet["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, "packet")
            item.setData(0, Qt.ItemDataRole.UserRole + 1, index)
            item.setToolTip(0, packet.get("description", ""))
            self.packet_list.addTopLevelItem(item)
        if selected_entry is not None:
            index = selected_entry[1]
            target = next(
                (
                    self.packet_list.topLevelItem(row)
                    for row in range(self.packet_list.topLevelItemCount())
                    if self.packet_list.topLevelItem(row).data(0, Qt.ItemDataRole.UserRole) == "packet"
                    and self.packet_list.topLevelItem(row).data(0, Qt.ItemDataRole.UserRole + 1) == index
                ),
                None,
            )
            if target is not None:
                self.packet_list.setCurrentItem(target)

    def refresh_sequence_catalog(self, selected_index: int | None = None) -> None:
        self.sequence_catalog_list.clear()
        for index, composition in enumerate(self.packet_compositions):
            item = QTreeWidgetItem([composition["display_name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setToolTip(0, str(composition["path"]))
            self.sequence_catalog_list.addTopLevelItem(item)
        if selected_index is not None and 0 <= selected_index < self.sequence_catalog_list.topLevelItemCount():
            self.sequence_catalog_list.setCurrentItem(self.sequence_catalog_list.topLevelItem(selected_index))

    def select_packet(self, index: int) -> None:
        self.show_packet(self.packets[index])

    def select_composition(self, index: int) -> None:
        composition = self.packet_compositions[index]
        self.packet_sequence = deepcopy(composition["packets"])
        self.sequence_mode = True
        self.current_sequence_name = composition["name"]
        self.current_sequence_path = composition.get("path")
        self.editing_sequence_index = index
        self.rebuild_sequence_list()
        self.packet_description.setText(composition["description"])
        if self.packet_sequence:
            self.send_structure_list.setCurrentItem(self.send_structure_list.topLevelItem(0))
            self.show_packet(self.packet_sequence[0])
        else:
            self.update_preview()
        self.statusBar().showMessage(f"已载入组合包 {composition['display_name']} 的发送序列")

    def show_packet(self, packet: dict[str, Any]) -> None:
        self.current_packet = packet
        sequence_index = next(
            (index for index, item in enumerate(self.packet_sequence) if item is packet),
            None,
        )
        try:
            if sequence_index is not None:
                self.current_size_values = packet_sequence_size_values(
                    self.packet_sequence, self.packet_catalog
                )[sequence_index]
            else:
                self.current_size_values = packet_sequence_size_values(
                    [packet], self.packet_catalog
                )[0]
        except (ValueError, KeyError, TypeError) as error:
            self.current_size_values = {}
            self.statusBar().showMessage(f"尺寸计算失败：{error}")
        self.packet_description.setText(self.current_packet.get("description", ""))
        self.rebuild_field_tree()
        self.update_preview()

    def packet_sequence_selected(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is not None:
            composition_index = current.data(0, Qt.ItemDataRole.UserRole)
            if composition_index < len(self.packet_compositions):
                self.select_composition(composition_index)

    def packet_sequence_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        composition_index = item.data(0, Qt.ItemDataRole.UserRole)
        if composition_index < len(self.packet_compositions) and (
            not self.sequence_mode or self.editing_sequence_index != composition_index
        ):
            self.select_composition(composition_index)

    def send_structure_selected(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if not self.sequence_mode or current is None:
            return
        index = current.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(index, int) and 0 <= index < len(self.packet_sequence):
            self.show_packet(self.packet_sequence[index])

    def sync_sequence_definition(self) -> None:
        if self.sequence_mode and self.editing_sequence_index is not None:
            self.packet_compositions[self.editing_sequence_index]["packets"] = deepcopy(
                self.packet_sequence
            )

    def create_packet_sequence(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建包序列", "序列名称：")
        if not accepted or not name.strip():
            return
        self.sequence_mode = True
        self.current_sequence_name = name.strip()
        self.current_sequence_path = None
        self.packet_sequence = []
        composition_index = self.register_composition(
            self.current_sequence_name, self.packet_sequence, None
        )
        self.editing_sequence_index = composition_index
        self.sequence_catalog_list.setCurrentItem(
            self.sequence_catalog_list.topLevelItem(composition_index)
        )
        self.rebuild_sequence_list()
        self.packet_description.setText(f"新建包序列：{self.current_sequence_name}")
        self.update_preview()
        self.statusBar().showMessage("序列已创建，选择包目录中的包后点击 + 添加")

    def add_packet_to_sequence(self) -> None:
        selected_items = sorted(
            self.packet_list.selectedItems(), key=self.packet_list.indexOfTopLevelItem
        )
        if not selected_items:
            self.statusBar().showMessage("请先在包目录中选择一个或多个包")
            return
        if self.editing_sequence_index is None:
            self.current_sequence_name = "packet_sequence"
            self.current_sequence_path = None
            self.editing_sequence_index = self.register_composition(
                self.current_sequence_name, [], None
            )
        composition = self.packet_compositions[self.editing_sequence_index]
        sequence_packets = deepcopy(composition["packets"])
        insert_index = len(sequence_packets)
        selected_item = self.send_structure_list.currentItem()
        if self.sequence_mode and selected_item is not None:
            selected_index = selected_item.data(0, Qt.ItemDataRole.UserRole)
            if selected_index is not None:
                insert_index = selected_index + 1
        for item in selected_items:
            packet_index = item.data(0, Qt.ItemDataRole.UserRole + 1)
            sequence_packets.insert(insert_index, deepcopy(self.packets[packet_index]))
            insert_index += 1
        composition["packets"] = sequence_packets
        self.sequence_mode = True
        self.current_sequence_name = composition["name"]
        self.current_sequence_path = composition.get("path")
        self.packet_sequence = deepcopy(sequence_packets)
        self.refresh_sequence_catalog(self.editing_sequence_index)
        self.rebuild_sequence_list()
        self.show_packet(self.packet_sequence[min(insert_index - 1, len(self.packet_sequence) - 1)])

    def remove_packet_from_sequence(self) -> None:
        if not self.sequence_mode:
            return
        selected_item = self.send_structure_list.currentItem()
        if selected_item is None:
            return
        remove_index = selected_item.data(0, Qt.ItemDataRole.UserRole)
        del self.packet_sequence[remove_index]
        if self.editing_sequence_index is not None:
            self.packet_compositions[self.editing_sequence_index]["packets"] = deepcopy(
                self.packet_sequence
            )
        if self.packet_sequence:
            self.rebuild_sequence_list()
            self.show_packet(self.packet_sequence[min(remove_index, len(self.packet_sequence) - 1)])
            return
        self.rebuild_sequence_list()

    def register_composition(
        self,
        name: str,
        packets: list[dict[str, Any]],
        path: Path | None,
    ) -> int:
        resolved_path = path.resolve() if path is not None else None
        composition = {
            "name": name,
            "display_name": name,
            "description": f"组合数据包 · {len(packets)} 个子包",
            "path": resolved_path,
            "packets": deepcopy(packets),
        }
        existing_index = next(
            (
                index
                for index, existing in enumerate(self.packet_compositions)
                if (
                    resolved_path is not None
                    and existing.get("path") is not None
                    and Path(existing["path"]).resolve() == resolved_path
                ) or (resolved_path is None and existing["name"] == name)
            ),
            None,
        )
        if existing_index is None:
            existing_index = len(self.packet_compositions)
            self.packet_compositions.append(composition)
        else:
            self.packet_compositions[existing_index] = composition
        self.refresh_sequence_catalog(existing_index)
        return existing_index

    def rebuild_sequence_list(self) -> None:
        self.send_structure_list.clear()
        for index, packet in enumerate(self.packet_sequence):
            item = QTreeWidgetItem([f"{index + 1:02d}  {packet['name']}"])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.send_structure_list.addTopLevelItem(item)
        if not self.packet_sequence and self.current_packet:
            if self.sequence_mode:
                item = QTreeWidgetItem(["（空序列）"])
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.send_structure_list.addTopLevelItem(item)
            else:
                self.send_structure_list.addTopLevelItem(QTreeWidgetItem([self.current_packet["name"]]))

    def rebuild_field_tree(self) -> None:
        self._building_tree = True
        self.field_tree.clear()
        for field in self.current_packet["fields"]:
            self.add_field_item(self.field_tree.invisibleRootItem(), field)
        self.field_tree.expandAll()
        self._building_tree = False

    def add_field_item(self, parent: QTreeWidgetItem, field: dict[str, Any]) -> None:
        kind = field.get("kind", "scalar")
        c_type = field["type"]
        name = field["name"]
        comment = field.get("comment", "")
        if kind == "scalar":
            item = QTreeWidgetItem([
                c_type,
                name,
                comment,
                self.format_value(field["value"], self.current_size_values),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, (field, None))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            parent.addChild(item)
        elif kind == "array":
            values = field.get("items", field.get("values", []))
            item = QTreeWidgetItem([c_type, f"{name}[{len(values)}]", comment, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, (field, None))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            parent.addChild(item)
            if "items" in field:
                for child_field in field["items"]:
                    self.add_field_item(item, child_field)
            else:
                for index, value in enumerate(values):
                    child = QTreeWidgetItem([
                        c_type,
                        f"{name}[{index}]",
                        field.get("item_comment", ""),
                        self.format_value(value, self.current_size_values),
                    ])
                    child.setData(0, Qt.ItemDataRole.UserRole, (field, index))
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsEditable)
                    item.addChild(child)
        else:
            item = QTreeWidgetItem([c_type, name, comment, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, (field, None))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            parent.addChild(item)
            for child_field in field["fields"]:
                self.add_field_item(item, child_field)

    @staticmethod
    def format_value(value: Any, size_values: dict[str, int] | None = None) -> str:
        if isinstance(value, dict) and isinstance(value.get("dynamic"), str):
            expression = value["dynamic"]
            if size_values is not None and expression in size_values:
                unit = " B" if expression.startswith("size(") else ""
                return f"{expression} ({size_values[expression]}{unit})"
            return expression
        return f"{value:.7g}" if isinstance(value, float) else str(value)

    @staticmethod
    def parse_integer(text: str) -> int:
        value = text.strip().lower()
        unsigned = value.removeprefix("+").removeprefix("-")
        base = 0 if unsigned.startswith(("0x", "0b", "0o")) else 10
        return int(value, base)

    def field_edited(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building_tree:
            return
        binding = item.data(0, Qt.ItemDataRole.UserRole)
        if not binding:
            return
        field, index = binding
        if column == 2:
            if index is None:
                field["comment"] = item.text(2)
            else:
                field["item_comment"] = item.text(2)
            self.sync_sequence_definition()
            self.rebuild_field_tree()
            return
        if column != 3:
            self.rebuild_field_tree()
            return
        if index is None and field.get("kind", "scalar") != "scalar":
            self.rebuild_field_tree()
            return
        values = field["values"] if field.get("kind") == "array" else None
        previous = values[index] if values is not None else field["value"]
        try:
            text = item.text(3).strip()
            if field["type"] in INTEGER_TYPES:
                if isinstance(previous, dict) and "dynamic" in previous and text == self.format_value(
                    previous, self.current_size_values
                ):
                    return
                expression = text.removeprefix("=").strip()
                if expression.startswith("size("):
                    parse_size_expression(expression)
                    value = {"dynamic": expression}
                elif expression.startswith("crc("):
                    if not self.sequence_mode:
                        raise ValueError("CRC 表达式只能用于包序列")
                    parse_crc_expression(expression)
                    value = {"dynamic": expression}
                else:
                    unsigned_text = text.lower().removeprefix("+").removeprefix("-")
                    value = text if unsigned_text.startswith("0x") else self.parse_integer(text)
            else:
                value = float(text)
            if values is not None:
                values[index] = value
            else:
                field["value"] = value
            pack_fields(self.current_packet["fields"])
        except (ValueError, OverflowError, struct.error, KeyError, TypeError) as error:
            if values is not None:
                values[index] = previous
            else:
                field["value"] = previous
            self.statusBar().showMessage(f"字段值无效：{error}")
            self.rebuild_field_tree()
            return
        self.sync_sequence_definition()
        self.update_preview()

    def update_preview(self) -> bytes | None:
        try:
            if self.sequence_mode:
                payload = pack_packet_sequence(self.packet_sequence, self.packet_catalog)
                label = f"序列 {len(self.packet_sequence)} 包 · "
            else:
                payload = pack_packet_sequence([self.current_packet], self.packet_catalog)
                label = ""
        except (ValueError, OverflowError, struct.error, KeyError, TypeError) as error:
            self.hex_preview.setPlainText(f"编码失败：{error}")
            return None
        self.hex_preview.setPlainText(
            f"{label}长度 {len(payload)} 字节    " + payload.hex(" ").upper()
        )
        return payload

    def save_packet(self) -> None:
        if self.sequence_mode and not self.packet_sequence:
            QMessageBox.information(self, "空序列", "请先向包序列中添加至少一个包。")
            return
        payload = self.update_preview()
        if payload is None:
            QMessageBox.warning(self, "保存失败", "当前字段值无法编码，请检查数值范围。")
            return
        if self.sequence_mode:
            default_name = f"{self.current_sequence_name or 'packet_sequence'}.json"
            default_directory = (
                self.current_sequence_path.parent
                if self.current_sequence_path is not None
                else self.app_directory
            )
        else:
            default_name = f"{self.current_packet['name']}.json"
            default_directory = self.app_directory
        path, _ = QFileDialog.getSaveFileName(
            self, "保存数据包或序列", str(default_directory / default_name), "JSON 文件 (*.json)"
        )
        if not path:
            return
        document = {"byte_order": "little", "packing": 1}
        if self.sequence_mode:
            document["packet_sequence"] = deepcopy(self.packet_sequence)
        else:
            document["packet"] = self.current_packet
        try:
            save_packet_document(Path(path), document)
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        if self.sequence_mode:
            self.current_sequence_name = Path(path).stem
            self.current_sequence_path = Path(path)
            sequence_index = self.register_composition(
                self.current_sequence_name, self.packet_sequence, self.current_sequence_path
            )
            self.refresh_sequence_catalog(sequence_index)
            self.editing_sequence_index = sequence_index
            self.statusBar().showMessage(f"包序列已保存到 {path}")
        else:
            self.statusBar().showMessage(f"单包已保存到 {path}")

    def save_current_packet(self) -> None:
        packet_path = self.app_directory / f"{self.current_packet['name']}.json"
        for candidate in sorted(self.app_directory.rglob("*.json")):
            try:
                definitions = document_packet_definitions(read_packet_document(candidate))
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
            if any(packet["name"] == self.current_packet["name"] for packet in definitions):
                packet_path = candidate
                break
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存当前单包定义",
            str(packet_path),
            "JSON 文件 (*.json)",
        )
        if not path:
            return
        document = {
            "byte_order": "little",
            "packing": 1,
            "packet": self.current_packet,
        }
        try:
            save_packet_document(Path(path), document)
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return

        packet_copy = deepcopy(self.current_packet)
        self.packet_catalog[packet_copy["name"]] = packet_copy
        packet_index = next(
            (index for index, packet in enumerate(self.packets) if packet["name"] == packet_copy["name"]),
            None,
        )
        if packet_index is None:
            self.packets.append(packet_copy)
        else:
            self.packets[packet_index] = packet_copy
        self.sync_sequence_definition()
        self.statusBar().showMessage(f"单包定义已保存到 {path}")

    def open_packet(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开数据包", str(self.app_directory), "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            packets, is_sequence = load_packet_file(Path(path), self.packet_catalog)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error) as error:
            QMessageBox.critical(self, "打开失败", str(error))
            return

        if is_sequence:
            composition_index = self.register_composition(Path(path).stem, packets, Path(path))
            self.sequence_catalog_list.setCurrentItem(
                self.sequence_catalog_list.topLevelItem(composition_index)
            )
            self.select_composition(composition_index)
            return

        self.sequence_mode = False
        self.current_sequence_name = None
        self.current_sequence_path = None
        self.packet_sequence.clear()
        self.rebuild_sequence_list()

        selected_packet = packets[0]
        packet_index = next(
            (index for index, packet in enumerate(self.packets) if packet["name"] == selected_packet["name"]),
            None,
        )
        if packet_index is None:
            packet_index = len(self.packets)
            self.packets.append(selected_packet)
        else:
            self.packets[packet_index] = selected_packet
        self.packet_catalog[selected_packet["name"]] = selected_packet
        self.select_packet(packet_index)
        self.rebuild_sequence_list()
        self.refresh_packet_catalog(("packet", packet_index))
        self.statusBar().showMessage(f"已打开数据包：{selected_packet['name']}")

    def update_transmit_rate(self, frequency: float) -> None:
        if self.tx_timer.isActive():
            self.tx_timer.start(max(1, round(1000 / frequency)))

    def toggle_transmit(self, enabled: bool) -> None:
        if enabled and not self.serial_controller.is_open():
            self.transmit_button.setChecked(False)
            QMessageBox.information(self, "串口未打开", "请先打开串口，再开始循环发送。")
            return
        if enabled:
            self.tx_timer.start(max(1, round(1000 / self.frequency.value())))
            self.transmit_button.setText("停止循环发送")
            self.send_current_packet()
        else:
            self.tx_timer.stop()
            self.transmit_button.setText("开始循环发送")

    def send_current_packet(self) -> None:
        if not self.serial_controller.is_open():
            self.statusBar().showMessage("请先打开串口")
            return
        if self.sequence_mode and not self.packet_sequence:
            self.statusBar().showMessage("当前包序列为空")
            return
        payload = self.update_preview()
        if payload is None:
            self.tx_timer.stop()
            self.transmit_button.setChecked(False)
            return
        written = self.serial_controller.write(payload)
        if written < 0:
            self.statusBar().showMessage(
                f"发送失败：{self.serial_controller.error_string()}"
            )
            return
        self.append_log("TX", payload.hex(" ").upper())
        packet_names = (
            " + ".join(packet["name"] for packet in self.packet_sequence)
            if self.packet_sequence
            else self.current_packet["name"]
        )
        self.statusBar().showMessage(f"已发送 {written} 字节 · {packet_names}")

    def handle_serial_data(self, data: bytes) -> None:
        self.append_log("RX", f"{len(data)} B · {data.hex(' ').upper()}")
        self.frame_decoder.receive_buffer.extend(data)
        if not self.sequence_mode or not self.packet_sequence:
            return
        decoded_frames, messages = self.frame_decoder.decode(self.packet_sequence)
        for message in messages:
            self.append_log("WARN", message)
        for decoded in decoded_frames:
            self.sync_sequence_definition()
            self.append_log(
                "PARSED",
                f"已解码 {len(decoded['packets'])} 个包，共 {decoded['frame_size']} B",
            )
            self.show_packet(self.current_packet)

    def handle_serial_error(self, message: str, resource_error: bool) -> None:
        self.statusBar().showMessage(f"串口错误：{message}")
        if resource_error:
            self.tx_timer.stop()
            self.transmit_button.setChecked(False)
            self.serial_controller.close()
            self.connect_button.setText("打开串口")

    def append_log(self, direction: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.serial_log.append(f"{timestamp}  {direction:<4} {message}")

    def closeEvent(self, event) -> None:
        self.tx_timer.stop()
        if self.serial_controller.is_open():
            self.serial_controller.close()
        super().closeEvent(event)
