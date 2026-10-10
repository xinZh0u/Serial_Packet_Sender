"""Serial transport and incoming packet-frame decoding."""

import struct
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtSerialPort import QSerialPort

from protocol import pack_fields, parse_integer_value, size, unpack_fields


class SerialController(QObject):
    data_received = Signal(bytes)
    error_occurred = Signal(str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.port = QSerialPort(self)
        self.port.readyRead.connect(self._read_available)
        self.port.errorOccurred.connect(self._handle_error)

    def open(
        self,
        port_name: str,
        baud_rate: int,
        data_bits: str,
        parity: str,
        stop_bits: str,
    ) -> bool:
        self.port.setPortName(port_name)
        self.port.setBaudRate(baud_rate)
        self.port.setDataBits({
            "5": QSerialPort.DataBits.Data5,
            "6": QSerialPort.DataBits.Data6,
            "7": QSerialPort.DataBits.Data7,
            "8": QSerialPort.DataBits.Data8,
        }[data_bits])
        self.port.setParity({
            "N": QSerialPort.Parity.NoParity,
            "E": QSerialPort.Parity.EvenParity,
            "O": QSerialPort.Parity.OddParity,
        }[parity])
        self.port.setStopBits(
            QSerialPort.StopBits.OneStop
            if stop_bits == "1"
            else QSerialPort.StopBits.TwoStop
        )
        return self.port.open(QSerialPort.OpenModeFlag.ReadWrite)

    def close(self) -> None:
        self.port.close()

    def is_open(self) -> bool:
        return self.port.isOpen()

    def write(self, payload: bytes) -> int:
        return self.port.write(payload)

    def error_string(self) -> str:
        return self.port.errorString()

    def _read_available(self) -> None:
        data = bytes(self.port.readAll())
        if data:
            self.data_received.emit(data)

    def _handle_error(self, error: QSerialPort.SerialPortError) -> None:
        if error != QSerialPort.SerialPortError.NoError:
            self.error_occurred.emit(
                self.port.errorString(),
                error == QSerialPort.SerialPortError.ResourceError,
            )


class SequenceFrameDecoder:
    def __init__(self) -> None:
        self.receive_buffer = bytearray()

    @staticmethod
    def find_field(fields: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        for field in fields:
            if field.get("name") == name:
                return field
            if field.get("kind") == "struct":
                match = SequenceFrameDecoder.find_field(field["fields"], name)
                if match is not None:
                    return match
            if field.get("kind") == "array" and "items" in field:
                match = SequenceFrameDecoder.find_field(field["items"], name)
                if match is not None:
                    return match
        return None

    @staticmethod
    def merge_received_values(
        destination: list[dict[str, Any]], source: list[dict[str, Any]]
    ) -> None:
        for destination_field, source_field in zip(destination, source):
            kind = destination_field.get("kind", "scalar")
            if kind == "struct":
                SequenceFrameDecoder.merge_received_values(
                    destination_field["fields"], source_field["fields"]
                )
            elif kind == "array":
                if "items" in destination_field:
                    SequenceFrameDecoder.merge_received_values(
                        destination_field["items"], source_field["items"]
                    )
                else:
                    original_values = destination_field["values"]
                    decoded_values = source_field["values"]
                    destination_field["values"] = [
                        previous if isinstance(previous, dict) and "dynamic" in previous else decoded
                        for previous, decoded in zip(original_values, decoded_values)
                    ]
            else:
                previous = destination_field.get("value")
                if not (isinstance(previous, dict) and "dynamic" in previous):
                    destination_field["value"] = source_field["value"]

    def decode(
        self, packets: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if not packets:
            return [], []
        header_packet = packets[0]
        sof_field = self.find_field(header_packet["fields"], "SOF")
        length_field = self.find_field(header_packet["fields"], "data_length")
        if sof_field is None or length_field is None:
            return [], []
        try:
            sof = parse_integer_value(sof_field["value"])
            header_size = len(pack_fields(header_packet["fields"]))
        except (KeyError, TypeError, ValueError, struct.error):
            return [], []

        decoded_sequences: list[dict[str, Any]] = []
        messages: list[str] = []
        while self.receive_buffer:
            sync_offset = self.receive_buffer.find(bytes([sof]))
            if sync_offset < 0:
                self.receive_buffer.clear()
                break
            if sync_offset:
                del self.receive_buffer[:sync_offset]
            if len(self.receive_buffer) < header_size:
                break

            header_copy = deepcopy(header_packet)
            try:
                unpack_fields(
                    header_copy["fields"],
                    bytes(self.receive_buffer[:header_size]),
                )
                data_length = parse_integer_value(
                    self.find_field(header_copy["fields"], "data_length")["value"]
                )
            except (KeyError, TypeError, ValueError, struct.error):
                del self.receive_buffer[0]
                continue

            tail_size = size(packets[-1]) if packets[-1]["name"] == "frame_tail_t" else 0
            frame_size = header_size + data_length + tail_size
            if frame_size < header_size:
                del self.receive_buffer[0]
                continue
            if len(self.receive_buffer) < frame_size:
                break

            frame = bytes(self.receive_buffer[:frame_size])
            decoded_packets = deepcopy(packets)
            offset = 0
            try:
                for packet in decoded_packets:
                    offset = unpack_fields(packet["fields"], frame, offset)
            except (struct.error, KeyError, TypeError, ValueError):
                del self.receive_buffer[0]
                continue
            if offset != frame_size:
                messages.append(
                    f"帧长度不匹配：头部声明 {data_length} B，"
                    f"序列结构解码 {offset - header_size} B"
                )
                del self.receive_buffer[:frame_size]
                continue

            del self.receive_buffer[:frame_size]
            for current, decoded in zip(packets, decoded_packets):
                self.merge_received_values(current["fields"], decoded["fields"])
            decoded_sequences.append({
                "packets": packets,
                "frame_size": frame_size,
            })
        return decoded_sequences, messages
