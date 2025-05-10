#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python 系统残留清理工具
------------------------
一个用于检测和清理 Python 相关残留文件的图形化工具
包括 __pycache__ 目录、.pyc 文件、临时文件和未使用的虚拟环境等
"""

import os
import sys
import shutil
import time
from datetime import datetime
import platform
from pathlib import Path
import threading
import json
import re

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QTextEdit, 
                               QTreeWidget, QTreeWidgetItem, QCheckBox, 
                               QFileDialog, QProgressBar, QSplitter, QFrame, 
                               QTabWidget, QMessageBox, QComboBox, QSpinBox,
                               QGroupBox, QRadioButton)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QSize, QThread, QTimer
from PySide6.QtGui import QIcon, QFont, QColor, QPalette, QPixmap, QTextCursor


class SignalBridge(QObject):
    """信号桥接类，用于线程间通信"""
    log_signal = Signal(str, str)  # 参数: 消息, 级别(info, warning, error)
    found_item_signal = Signal(str, str, int, bool)  # 参数: 路径, 类型, 大小(字节), 是否默认选中
    scan_finished_signal = Signal(bool, int, float)  # 参数: 是否成功, 找到的项目数量, 总大小(MB)
    progress_signal = Signal(int, int)  # 参数: 当前进度, 总进度
    clean_progress_signal = Signal(int, int, str)  # 参数: 当前进度, 总进度, 当前处理的路径
    clean_finished_signal = Signal(bool, int, float)  # 参数: 是否成功, 清理的项目数量, 释放的空间(MB)


class Scanner(QThread):
    """扫描器线程类"""
    
    def __init__(self, signals, scan_paths, scan_options):
        super().__init__()
        self.signals = signals
        self.scan_paths = scan_paths
        self.scan_options = scan_options
        self.running = True
    
    def stop(self):
        """停止扫描"""
        self.running = False
    
    def run(self):
        """运行扫描线程"""
        try:
            total_items = 0
            total_size = 0
            
            self.signals.log_signal.emit("开始扫描...", "info")
            
            # 获取所有扫描路径
            paths_to_scan = []
            for path in self.scan_paths:
                if os.path.exists(path):
                    paths_to_scan.append(path)
                else:
                    self.signals.log_signal.emit(f"路径不存在: {path}", "warning")
            
            if not paths_to_scan:
                self.signals.log_signal.emit("没有有效的扫描路径", "error")
                self.signals.scan_finished_signal.emit(False, 0, 0)
                return
            
            # 开始扫描
            for base_path in paths_to_scan:
                self.signals.log_signal.emit(f"扫描路径: {base_path}", "info")
                
                # 计算要扫描的文件和目录的大致数量
                total_count = 0
                for root, dirs, files in os.walk(base_path):
                    if not self.running:
                        break
                    total_count += len(dirs) + len(files)
                
                current_count = 0
                
                # 遍历目录
                for root, dirs, files in os.walk(base_path):
                    if not self.running:
                        break
                    
                    # 更新进度
                    current_count += len(dirs) + len(files)
                    if total_count > 0:
                        progress = int((current_count / total_count) * 100)
                        self.signals.progress_signal.emit(progress, 100)
                    
                    # 检查各种 Python 残留文件
                    
                    # 1. __pycache__ 目录
                    if self.scan_options.get('pycache', False):
                        for dir_name in dirs[:]:
                            if dir_name == "__pycache__":
                                dir_path = os.path.join(root, dir_name)
                                size = self._get_dir_size(dir_path)
                                total_items += 1
                                total_size += size
                                self.signals.found_item_signal.emit(
                                    dir_path, "pycache", size, True
                                )
                    
                    # 2. .pyc 文件
                    if self.scan_options.get('pyc_files', False):
                        for file_name in files:
                            if file_name.endswith(".pyc"):
                                file_path = os.path.join(root, file_name)
                                size = os.path.getsize(file_path)
                                total_items += 1
                                total_size += size
                                self.signals.found_item_signal.emit(
                                    file_path, "pyc", size, True
                                )
                    
                    # 3. 废弃的虚拟环境
                    if self.scan_options.get('venv', False):
                        # 检查是否是虚拟环境目录
                        is_venv = False
                        venv_markers = ["pyvenv.cfg", "activate", "python.exe", "pip.exe"]
                        for marker in venv_markers:
                            if (os.path.exists(os.path.join(root, marker)) or 
                                os.path.exists(os.path.join(root, "bin", marker)) or
                                os.path.exists(os.path.join(root, "Scripts", marker))):
                                is_venv = True
                                break
                        
                        if is_venv:
                            # 检查虚拟环境的最后访问时间，超过指定时间视为废弃
                            last_access_time = max(
                                os.path.getatime(os.path.join(root, f)) 
                                for f in os.listdir(root) 
                                if os.path.isfile(os.path.join(root, f))
                            )
                            days_since_access = (time.time() - last_access_time) / (60 * 60 * 24)
                            
                            if days_since_access > self.scan_options.get('venv_days', 30):
                                size = self._get_dir_size(root)
                                total_items += 1
                                total_size += size
                                self.signals.found_item_signal.emit(
                                    root, "venv", size, False  # 默认不选中，需用户确认
                                )
                    
                    # 4. Jupyter 缓存和检查点
                    if self.scan_options.get('jupyter', False):
                        for dir_name in dirs[:]:
                            if dir_name == ".ipynb_checkpoints":
                                dir_path = os.path.join(root, dir_name)
                                size = self._get_dir_size(dir_path)
                                total_items += 1
                                total_size += size
                                self.signals.found_item_signal.emit(
                                    dir_path, "jupyter", size, True
                                )
                    
                    # 5. 临时 Python 文件
                    if self.scan_options.get('temp_files', False):
                        for file_name in files:
                            if file_name.endswith((".pyc.tmp", ".py~", ".pyo")):
                                file_path = os.path.join(root, file_name)
                                size = os.path.getsize(file_path)
                                total_items += 1
                                total_size += size
                                self.signals.found_item_signal.emit(
                                    file_path, "temp", size, True
                                )
                                
                    # 6. Python 构建文件和分发目录
                    if self.scan_options.get('build_dirs', False):
                        for dir_name in dirs[:]:
                            if dir_name in ["build", "dist", "*.egg-info"]:
                                dir_path = os.path.join(root, dir_name)
                                size = self._get_dir_size(dir_path)
                                total_items += 1
                                total_size += size
                                self.signals.found_item_signal.emit(
                                    dir_path, "build", size, True
                                )
            
            self.signals.log_signal.emit(
                f"扫描完成，找到 {total_items} 个项目，总大小: {self._format_size(total_size)}", 
                "info"
            )
            # 将字节转换为MB以避免整数溢出
            total_size_mb = total_size / (1024 * 1024)
            self.signals.scan_finished_signal.emit(True, total_items, total_size_mb)
            
        except Exception as e:
            self.signals.log_signal.emit(f"扫描过程中发生错误: {str(e)}", "error")
            self.signals.scan_finished_signal.emit(False, total_items, total_size)
    
    def _get_dir_size(self, path):
        """获取目录大小"""
        total = 0
        try:
            for entry in os.scandir(path):
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += self._get_dir_size(entry.path)
        except (PermissionError, FileNotFoundError):
            pass
        return total
    
    def _format_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes/1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes/(1024*1024):.2f} MB"
        else:
            return f"{size_bytes/(1024*1024*1024):.2f} GB"


class Cleaner(QThread):
    """清理器线程类"""
    
    def __init__(self, signals, items_to_clean):
        super().__init__()
        self.signals = signals
        self.items_to_clean = items_to_clean
        self.running = True
    
    def stop(self):
        """停止清理"""
        self.running = False
    
    def run(self):
        """运行清理线程"""
        try:
            total_items = len(self.items_to_clean)
            total_size_cleaned = 0
            items_cleaned = 0
            
            self.signals.log_signal.emit("开始清理...", "info")
            
            for index, (path, type_name, size) in enumerate(self.items_to_clean):
                if not self.running:
                    break
                
                # 更新进度
                self.signals.clean_progress_signal.emit(index + 1, total_items, path)
                
                try:
                    if os.path.isdir(path):
                        self.signals.log_signal.emit(f"正在删除目录: {path}", "info")
                        shutil.rmtree(path)
                    elif os.path.isfile(path):
                        self.signals.log_signal.emit(f"正在删除文件: {path}", "info")
                        os.remove(path)
                    
                    total_size_cleaned += size
                    items_cleaned += 1
                    
                except (PermissionError, FileNotFoundError, OSError) as e:
                    self.signals.log_signal.emit(f"清理 {path} 时出错: {str(e)}", "error")
            
            self.signals.log_signal.emit(
                f"清理完成，共清理 {items_cleaned} 个项目，释放空间: {self._format_size(total_size_cleaned)}", 
                "info"
            )
            # 将字节转换为MB以避免整数溢出
            total_size_cleaned_mb = total_size_cleaned / (1024 * 1024)
            self.signals.clean_finished_signal.emit(True, items_cleaned, total_size_cleaned_mb)
            
        except Exception as e:
            self.signals.log_signal.emit(f"清理过程中发生错误: {str(e)}", "error")
            self.signals.clean_finished_signal.emit(False, items_cleaned, total_size_cleaned)
    
    def _format_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes/1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes/(1024*1024):.2f} MB"
        else:
            return f"{size_bytes/(1024*1024*1024):.2f} GB"


class PythonCleanerUI(QMainWindow):
    """Python 系统残留清理工具主界面"""
    
    def __init__(self):
        super().__init__()
        
        # 设置窗口基本属性
        self.setWindowTitle("Python 系统残留清理工具")
        self.setMinimumSize(1000, 700)
        
        # 初始化信号桥接
        self.signals = SignalBridge()
        self.signals.log_signal.connect(self.add_log)
        self.signals.found_item_signal.connect(self.add_found_item)
        self.signals.scan_finished_signal.connect(self.on_scan_finished)
        self.signals.progress_signal.connect(self.update_progress)
        self.signals.clean_progress_signal.connect(self.update_clean_progress)
        self.signals.clean_finished_signal.connect(self.on_clean_finished)
        
        # 初始化扫描器和清理器
        self.scanner = None
        self.cleaner = None
        
        # 初始化数据
        self.found_items = []  # 存储找到的项目 (路径, 类型, 大小)
        
        # 设置界面
        self.setup_ui()
        
        # 设置默认扫描路径
        self._set_default_scan_paths()
        
        # 显示欢迎信息
        self.add_log("欢迎使用 Python 系统残留清理工具", "info")
        self.add_log(f"系统: {platform.system()} {platform.version()}", "info")
        self.add_log(f"Python 版本: {platform.python_version()}", "info")
        self.add_log("请选择扫描选项并点击 \"开始扫描\" 按钮", "info")
    
    def setup_ui(self):
        """设置用户界面"""
        # 创建中央窗口部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 创建分割器，左侧控制面板，右侧结果显示
        splitter = QSplitter(Qt.Horizontal)
        
        # ===== 左侧控制面板 =====
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        
        # 扫描选项组
        scan_options_group = QGroupBox("扫描选项")
        scan_options_layout = QVBoxLayout(scan_options_group)
        
        # 路径选择
        self.path_edit = QTextEdit()
        self.path_edit.setPlaceholderText("输入要扫描的路径 (每行一个)")
        self.path_edit.setMaximumHeight(80)
        
        path_button_layout = QHBoxLayout()
        self.add_path_button = QPushButton("添加路径")
        self.add_path_button.clicked.connect(self.add_scan_path)
        path_button_layout.addWidget(self.add_path_button)
        
        self.default_paths_button = QPushButton("默认路径")
        self.default_paths_button.clicked.connect(self._set_default_scan_paths)
        path_button_layout.addWidget(self.default_paths_button)
        
        scan_options_layout.addWidget(QLabel("扫描路径:"))
        scan_options_layout.addWidget(self.path_edit)
        scan_options_layout.addLayout(path_button_layout)
        
        # 扫描选项复选框
        self.pycache_check = QCheckBox("__pycache__ 目录")
        self.pycache_check.setChecked(True)
        scan_options_layout.addWidget(self.pycache_check)
        
        self.pyc_files_check = QCheckBox(".pyc 文件")
        self.pyc_files_check.setChecked(True)
        scan_options_layout.addWidget(self.pyc_files_check)
        
        venv_layout = QHBoxLayout()
        self.venv_check = QCheckBox("未使用的虚拟环境")
        self.venv_check.setChecked(True)
        venv_layout.addWidget(self.venv_check)
        
        venv_layout.addWidget(QLabel("(超过"))
        self.venv_days_spin = QSpinBox()
        self.venv_days_spin.setRange(1, 365)
        self.venv_days_spin.setValue(30)
        venv_layout.addWidget(self.venv_days_spin)
        venv_layout.addWidget(QLabel("天未访问)"))
        venv_layout.addStretch()
        
        scan_options_layout.addLayout(venv_layout)
        
        self.jupyter_check = QCheckBox("Jupyter 缓存和检查点")
        self.jupyter_check.setChecked(True)
        scan_options_layout.addWidget(self.jupyter_check)
        
        self.temp_files_check = QCheckBox("临时 Python 文件")
        self.temp_files_check.setChecked(True)
        scan_options_layout.addWidget(self.temp_files_check)
        
        self.build_dirs_check = QCheckBox("构建目录 (build, dist, *.egg-info)")
        self.build_dirs_check.setChecked(True)
        scan_options_layout.addWidget(self.build_dirs_check)
        
        left_layout.addWidget(scan_options_group)
        
        # 操作按钮组
        actions_group = QGroupBox("操作")
        actions_layout = QVBoxLayout(actions_group)
        
        # 扫描按钮
        self.scan_button = QPushButton("开始扫描")
        self.scan_button.clicked.connect(self.start_scan)
        self.scan_button.setMinimumHeight(40)
        actions_layout.addWidget(self.scan_button)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        actions_layout.addWidget(self.progress_bar)
        
        # 清理按钮
        self.clean_button = QPushButton("清理选中项目")
        self.clean_button.clicked.connect(self.clean_selected_items)
        self.clean_button.setEnabled(False)
        self.clean_button.setMinimumHeight(40)
        actions_layout.addWidget(self.clean_button)
        
        # 项目选择按钮
        select_buttons_layout = QHBoxLayout()
        
        self.select_all_button = QPushButton("全选")
        self.select_all_button.clicked.connect(self.select_all_items)
        self.select_all_button.setEnabled(False)
        select_buttons_layout.addWidget(self.select_all_button)
        
        self.deselect_all_button = QPushButton("取消全选")
        self.deselect_all_button.clicked.connect(self.deselect_all_items)
        self.deselect_all_button.setEnabled(False)
        select_buttons_layout.addWidget(self.deselect_all_button)
        
        actions_layout.addLayout(select_buttons_layout)
        
        left_layout.addWidget(actions_group)
        
        # 日志区域
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        
        left_layout.addWidget(log_group)
        
        # ===== 右侧结果面板 =====
        right_panel = QTabWidget()
        
        # 扫描结果选项卡
        results_tab = QWidget()
        results_layout = QVBoxLayout(results_tab)
        results_layout.setContentsMargins(5, 5, 5, 5)
        
        # 结果树
        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabels(["选择", "路径", "类型", "大小"])
        self.results_tree.setColumnWidth(0, 50)  # 选择列宽
        self.results_tree.setColumnWidth(1, 400)  # 路径列宽
        self.results_tree.setColumnWidth(2, 100)  # 类型列宽
        self.results_tree.setAlternatingRowColors(True)
        
        results_layout.addWidget(self.results_tree)
        
        # 结果统计
        self.result_stats_label = QLabel("暂无扫描结果")
        results_layout.addWidget(self.result_stats_label)
        
        right_panel.addTab(results_tab, "扫描结果")
        
        # 代码选项卡
        code_tab = QWidget()
        code_layout = QVBoxLayout(code_tab)
        code_layout.setContentsMargins(5, 5, 5, 5)
        
        self.code_text = QTextEdit()
        self.code_text.setReadOnly(True)
        self.code_text.setFont(QFont("Consolas", 10))
        
        # 展示完整代码
        with open(__file__, "r", encoding="utf-8") as f:
            self.code_text.setText(f.read())
        
        code_layout.addWidget(self.code_text)
        
        # 保存按钮
        self.save_code_button = QPushButton("保存代码至文件")
        self.save_code_button.clicked.connect(self.save_code_to_file)
        code_layout.addWidget(self.save_code_button)
        
        right_panel.addTab(code_tab, "代码")
        
        # 将面板添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        
        # 设置分割器初始大小
        splitter.setSizes([300, 700])
        
        # 将分割器添加到主布局
        main_layout.addWidget(splitter)
        
        # 状态栏
        self.statusBar().showMessage("就绪")
    
    def _set_default_scan_paths(self):
        """设置默认扫描路径"""
        default_paths = []
        
        # 用户主目录
        default_paths.append(str(Path.home()))
        
        # Python 安装目录
        python_path = os.path.dirname(sys.executable)
        default_paths.append(python_path)
        
        # 系统临时目录
        temp_dir = os.environ.get('TEMP', '') or os.environ.get('TMP', '')
        if temp_dir:
            default_paths.append(temp_dir)
        
        # 设置到文本框
        self.path_edit.setText("\n".join(default_paths))
    
    def add_scan_path(self):
        """添加扫描路径"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择扫描目录", str(Path.home())
        )
        if dir_path:
            current_paths = self.path_edit.toPlainText().strip()
            if current_paths:
                self.path_edit.setText(f"{current_paths}\n{dir_path}")
            else:
                self.path_edit.setText(dir_path)
    
    def add_log(self, message, level="info"):
        """添加日志条目"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # 根据日志级别设置颜色
        color = "black"
        if level == "warning":
            color = "orange"
        elif level == "error":
            color = "red"
        elif level == "success":
            color = "green"
        
        # 添加带格式的日志
        self.log_text.append(f"<span style='color:{color}'>[{timestamp}] {message}</span>")
        
        # 滚动到底部
        self.log_text.moveCursor(QTextCursor.End)
        
        # 更新状态栏
        if level == "error":
            self.statusBar().showMessage(f"错误: {message}")
        elif level != "info":
            self.statusBar().showMessage(message)
    
    def start_scan(self):
        """开始扫描"""
        # 清空先前的结果
        self.results_tree.clear()
        self.found_items = []
        self.result_stats_label.setText("正在扫描...")
        
        # 禁用扫描按钮，启用取消按钮
        self.scan_button.setText("取消扫描")
        self.scan_button.clicked.disconnect()
        self.scan_button.clicked.connect(self.stop_scan)
        
        # 禁用清理按钮
        self.clean_button.setEnabled(False)
        self.select_all_button.setEnabled(False)
        self.deselect_all_button.setEnabled(False)
        
        # 重置进度条
        self.progress_bar.setValue(0)
        
        # 获取扫描路径
        scan_paths = [
            path.strip() for path in self.path_edit.toPlainText().split("\n") 
            if path.strip()
        ]
        
        if not scan_paths:
            self.add_log("请至少添加一个扫描路径", "error")
            self.reset_scan_ui()
            return
        
        # 获取扫描选项
        scan_options = {
            'pycache': self.pycache_check.isChecked(),
            'pyc_files': self.pyc_files_check.isChecked(),
            'venv': self.venv_check.isChecked(),
            'venv_days': self.venv_days_spin.value(),
            'jupyter': self.jupyter_check.isChecked(),
            'temp_files': self.temp_files_check.isChecked(),
            'build_dirs': self.build_dirs_check.isChecked()
        }
        
        # 创建并启动扫描线程
        self.scanner = Scanner(self.signals, scan_paths, scan_options)
        self.scanner.start()
    
    def stop_scan(self):
        """停止扫描"""
        if self.scanner and self.scanner.isRunning():
            self.scanner.stop()
            self.add_log("正在停止扫描...", "warning")
    
    def reset_scan_ui(self):
        """重置扫描 UI"""
        self.scan_button.setText("开始扫描")
        self.scan_button.clicked.disconnect()
        self.scan_button.clicked.connect(self.start_scan)
        
        # 根据结果启用相应按钮
        if self.found_items:
            self.clean_button.setEnabled(True)
            self.select_all_button.setEnabled(True)
            self.deselect_all_button.setEnabled(True)
    
    def add_found_item(self, path, type_name, size, checked):
        """添加找到的项目到结果树"""
        # 创建树项
        item = QTreeWidgetItem(self.results_tree)
        
        # 设置复选框
        item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        
        # 设置路径和类型
        item.setText(1, path)
        
        # 设置类型友好名称
        type_friendly_names = {
            "pycache": "__pycache__ 目录",
            "pyc": ".pyc 文件",
            "venv": "虚拟环境",
            "jupyter": "Jupyter 缓存",
            "temp": "临时文件",
            "build": "构建目录"
        }
        item.setText(2, type_friendly_names.get(type_name, type_name))
        
        # 设置大小
        item.setText(3, self._format_size(size))
        
        # 存储原始大小和类型
        item.setData(3, Qt.UserRole, size)
        item.setData(2, Qt.UserRole, type_name)
        
        # 添加到列表
        self.found_items.append((path, type_name, size))
    
    def on_scan_finished(self, success, count, total_size_mb):
        """扫描完成回调"""
        self.reset_scan_ui()
        
        if success:
            # 将MB转回字节以保持显示一致性
            total_size = int(total_size_mb * 1024 * 1024)
            
            # 更新结果统计
            self.result_stats_label.setText(
                f"找到 {count} 个项目，总大小: {self._format_size(total_size)}"
            )
            
            if count == 0:
                self.add_log("未找到任何 Python 残留文件", "info")
            else:
                self.add_log(f"扫描完成，找到 {count} 个项目", "success")
        else:
            self.add_log("扫描未完成", "warning")
    
    def update_progress(self, current, total):
        """更新进度条"""
        self.progress_bar.setValue(current)
        self.statusBar().showMessage(f"扫描进度: {current}%")
    
    def update_clean_progress(self, current, total, path):
        """更新清理进度"""
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.statusBar().showMessage(f"清理进度: {progress}% - {path}")
    
    def select_all_items(self):
        """选择所有项目"""
        for i in range(self.results_tree.topLevelItemCount()):
            item = self.results_tree.topLevelItem(i)
            item.setCheckState(0, Qt.Checked)
    
    def deselect_all_items(self):
        """取消选择所有项目"""
        for i in range(self.results_tree.topLevelItemCount()):
            item = self.results_tree.topLevelItem(i)
            item.setCheckState(0, Qt.Unchecked)
    
    def clean_selected_items(self):
        """清理选中的项目"""
        # 收集选中的项目
        items_to_clean = []
        
        for i in range(self.results_tree.topLevelItemCount()):
            item = self.results_tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                path = item.text(1)
                type_name = item.data(2, Qt.UserRole)
                size = item.data(3, Qt.UserRole)
                items_to_clean.append((path, type_name, size))
        
        if not items_to_clean:
            self.add_log("未选择任何项目进行清理", "warning")
            return
        
        # 确认对话框
        reply = QMessageBox.question(
            self, "确认清理", 
            f"确定要清理选中的 {len(items_to_clean)} 个项目吗？\n这将永久删除这些文件和目录。",
            QMessageBox.Yes | QMessageBox.No, 
            QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            return
        
        # 禁用按钮
        self.clean_button.setText("正在清理...")
        self.clean_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.select_all_button.setEnabled(False)
        self.deselect_all_button.setEnabled(False)
        
        # 创建并启动清理线程
        self.cleaner = Cleaner(self.signals, items_to_clean)
        self.cleaner.start()
    
    def on_clean_finished(self, success, count, total_size_mb):
        """清理完成回调"""
        # 重置 UI
        self.clean_button.setText("清理选中项目")
        self.clean_button.setEnabled(True)
        self.scan_button.setEnabled(True)
        self.select_all_button.setEnabled(True)
        self.deselect_all_button.setEnabled(True)
        
        if success:
            # 将MB转回字节以保持显示一致性
            total_size = int(total_size_mb * 1024 * 1024)
            
            # 更新状态
            self.add_log(
                f"清理完成，已清理 {count} 个项目，释放空间: {self._format_size(total_size)}", 
                "success"
            )
            
            # 重新扫描以更新结果
            self.start_scan()
        else:
            self.add_log("清理未完成", "warning")
    
    def save_code_to_file(self):
        """保存代码到文件"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存代码", 
            str(Path.home() / "python_cleaner.py"),
            "Python 文件 (*.py)"
        )
        
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(self.code_text.toPlainText())
                self.add_log(f"代码已保存至: {file_path}", "success")
            except Exception as e:
                self.add_log(f"保存代码时出错: {str(e)}", "error")
    
    def _format_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes/1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes/(1024*1024):.2f} MB"
        else:
            return f"{size_bytes/(1024*1024*1024):.2f} GB"


if __name__ == "__main__":
    # 创建应用
    app = QApplication(sys.argv)
    
    # 设置应用样式
    app.setStyle("Fusion")
    
    # 创建主窗口
    window = PythonCleanerUI()
    window.show()
    
    # 运行应用
    sys.exit(app.exec())
