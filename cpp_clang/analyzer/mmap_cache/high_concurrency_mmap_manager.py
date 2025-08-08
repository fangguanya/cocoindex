#!/usr/bin/env python3
"""
高并发mmap管理器 - 支持大并发访问的内存映射文件管理
"""

import os
import sys
import mmap
import struct
import threading
import time
import hashlib
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum

# 添加父目录到路径以导入logger
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from logger import get_logger


class MmapFileType(Enum):
    """mmap文件类型枚举"""
    CLASS_CACHE = "class_cache"
    HEADER_CACHE = "header_cache"
    FILE_CACHE = "file_cache"
    TEMPLATE_CACHE = "template_cache"


@dataclass
class MmapHeader:
    """mmap文件头部结构"""
    magic: bytes = b'MMAP'  # 魔数
    version: int = 1  # 版本号
    file_type: str = ""  # 文件类型
    created_time: float = field(default_factory=time.time)
    last_modified: float = field(default_factory=time.time)
    data_size: int = 0  # 数据大小
    index_offset: int = 0  # 索引偏移
    index_size: int = 0  # 索引大小
    checksum: bytes = b''  # 校验和
    reserved: bytes = b'\x00' * 64  # 保留字段
    
    HEADER_SIZE = 176  # 头部固定大小
    
    def pack(self) -> bytes:
        """打包头部数据"""
        # 确保checksum是正确长度的bytes
        checksum = self.checksum if len(self.checksum) == 32 else self.checksum.ljust(32, b'\x00')[:32]
        
        return struct.pack(
            '<4sI32sddQQQ32s64s',
            self.magic,
            self.version,
            self.file_type.encode('utf-8').ljust(32, b'\x00')[:32],  # 确保字段长度正确
            self.created_time,
            self.last_modified,
            self.data_size,
            self.index_offset,
            self.index_size,
            checksum,
            self.reserved
        )
    
    @classmethod
    def unpack(cls, data: bytes) -> 'MmapHeader':
        """解包头部数据"""
        if len(data) < cls.HEADER_SIZE:
            raise ValueError(f"头部数据长度不足: {len(data)} < {cls.HEADER_SIZE}")
        
        values = struct.unpack('<4sI32sddQQQ32s64s', data[:cls.HEADER_SIZE])
        header = cls()
        header.magic = values[0]
        header.version = values[1]
        header.file_type = values[2].decode('utf-8').rstrip('\x00')
        header.created_time = values[3]
        header.last_modified = values[4]
        header.data_size = values[5]
        header.index_offset = values[6]
        header.index_size = values[7]
        header.checksum = values[8]
        header.reserved = values[9]
        
        return header


@dataclass
class MmapIndexEntry:
    """mmap索引条目"""
    key_hash: bytes  # 键的哈希值
    data_offset: int  # 数据偏移
    data_size: int  # 数据大小
    timestamp: float  # 时间戳
    flags: int  # 标志位
    
    ENTRY_SIZE = 32  # 索引条目固定大小
    
    def pack(self) -> bytes:
        """打包索引条目"""
        # 确保key_hash是正确长度的bytes (16字节)
        key_hash = self.key_hash if len(self.key_hash) == 16 else self.key_hash.ljust(16, b'\x00')[:16]
        
        return struct.pack('<16sQQfI', key_hash, self.data_offset, 
                          self.data_size, self.timestamp, self.flags)
    
    @classmethod
    def unpack(cls, data: bytes) -> 'MmapIndexEntry':
        """解包索引条目"""
        if len(data) < cls.ENTRY_SIZE:
            raise ValueError(f"索引条目数据长度不足: {len(data)} < {cls.ENTRY_SIZE}")
        
        values = struct.unpack('<16sQQfI', data[:cls.ENTRY_SIZE])
        return cls(
            key_hash=values[0],
            data_offset=values[1],
            data_size=values[2],
            timestamp=values[3],
            flags=values[4]
        )


class MmapAccessMode(Enum):
    """mmap访问模式"""
    READ_ONLY = "r"
    READ_WRITE = "r+"
    WRITE_ONLY = "w+"


class HighConcurrencyMmapManager:
    """高并发mmap管理器"""
    
    def __init__(self, project_root: str, cache_dir: Optional[str] = None):
        self.logger = get_logger()
        self.project_root = project_root
        self.cache_dir = cache_dir or os.path.join(project_root, ".mmap_cache")
        
        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 文件映射表
        self.file_mappings: Dict[str, mmap.mmap] = {}
        self.file_headers: Dict[str, MmapHeader] = {}
        self.file_indexes: Dict[str, Dict[bytes, MmapIndexEntry]] = {}
        
        # 线程安全
        self._lock = threading.RLock()
        self._file_locks: Dict[str, threading.RLock] = {}
        
        # 性能统计
        self.stats = {
            'files_created': 0,
            'files_opened': 0,
            'read_operations': 0,
            'write_operations': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'errors': 0
        }
        
        # 配置参数
        self.initial_file_size = 1024 * 1024  # 1MB初始大小
        self.growth_factor = 2.0  # 增长因子
        self.max_file_size = 1024 * 1024 * 1024  # 1GB最大大小
        
        self.logger.info(f"高并发mmap管理器初始化完成，缓存目录: {self.cache_dir}")
    
    def _get_file_path(self, file_type: MmapFileType, shard_id: int = 0) -> str:
        """获取文件路径"""
        filename = f"{file_type.value}_shard_{shard_id:04d}.mmap"
        return os.path.join(self.cache_dir, filename)
    
    def _get_file_lock(self, file_path: str) -> threading.RLock:
        """获取文件锁"""
        if file_path not in self._file_locks:
            with self._lock:
                if file_path not in self._file_locks:
                    self._file_locks[file_path] = threading.RLock()
        return self._file_locks[file_path]
    
    def _calculate_checksum(self, data: bytes) -> bytes:
        """计算校验和"""
        return hashlib.md5(data).digest()
    
    def _validate_header(self, header: MmapHeader) -> bool:
        """验证文件头部"""
        if header.magic != b'MMAP':
            self.logger.error(f"无效的魔数: {header.magic}")
            return False
        
        if header.version != 1:
            self.logger.error(f"不支持的版本: {header.version}")
            return False
        
        return True
    
    def create_mmap_file(self, file_type: MmapFileType, shard_id: int = 0, 
                        initial_size: Optional[int] = None) -> bool:
        """创建mmap文件"""
        file_path = self._get_file_path(file_type, shard_id)
        file_lock = self._get_file_lock(file_path)
        
        with file_lock:
            try:
                if os.path.exists(file_path):
                    self.logger.warning(f"文件已存在: {file_path}")
                    return True
                
                # 计算初始大小
                size = initial_size or self.initial_file_size
                size = self._align_size(size)
                
                # 确保最小大小至少为64KB，避免数据区域过小
                min_size = 64 * 1024  # 64KB最小大小
                if size < min_size:
                    size = min_size
                    self.logger.warning(f"调整文件大小到最小值: {size}")
                
                # 创建文件并预分配空间
                with open(file_path, 'wb') as f:
                    f.write(b'\x00' * size)
                
                # 设置文件权限（确保可读写）
                import stat
                try:
                    os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
                    self.logger.debug(f"设置文件权限: {file_path}")
                except Exception as e:
                    self.logger.warning(f"设置文件权限失败: {file_path} - {e}")
                
                # 规划文件布局
                # Header: 0 - 176
                # Data区域: 176 - (size - 32000)  预留32KB给索引
                # Index区域: (size - 32000) - size
                index_reserved_size = 32000  # 32KB索引预留空间
                data_area_start = MmapHeader.HEADER_SIZE
                index_area_start = size - index_reserved_size
                
                # 创建头部
                header = MmapHeader(
                    file_type=file_type.value,
                    data_size=0,
                    index_offset=index_area_start,  # 索引在文件末尾
                    index_size=0
                )
                
                self.logger.info(f"初始化文件布局: 总大小={size}, 头部={MmapHeader.HEADER_SIZE}, 数据区域={data_area_start}-{index_area_start}, 索引区域={index_area_start}-{size}")
                self.logger.info(f"创建头部: index_offset={header.index_offset}, file_type={header.file_type}")
                
                # 写入头部
                with open(file_path, 'r+b') as f:
                    header_bytes = header.pack()
                    f.write(header_bytes)
                    f.flush()
                    
                # 验证头部写入
                with open(file_path, 'rb') as f:
                    verify_data = f.read(MmapHeader.HEADER_SIZE)
                    verify_header = MmapHeader.unpack(verify_data)
                    self.logger.info(f"验证头部: index_offset={verify_header.index_offset}, magic={verify_header.magic}")
                
                self.stats['files_created'] += 1
                self.logger.info(f"创建mmap文件: {file_path} (大小: {size})")
                return True
                
            except Exception as e:
                self.stats['errors'] += 1
                self.logger.error(f"创建mmap文件失败: {file_path} - {e}")
                return False
    
    def open_mmap_file(self, file_type: MmapFileType, shard_id: int = 0, 
                      mode: MmapAccessMode = MmapAccessMode.READ_WRITE) -> bool:
        """打开mmap文件"""
        file_path = self._get_file_path(file_type, shard_id)
        file_lock = self._get_file_lock(file_path)
        
        with file_lock:
            try:
                if file_path in self.file_mappings:
                    self.logger.debug(f"文件已打开: {file_path}")
                    return True
                
                if not os.path.exists(file_path):
                    self.logger.warning(f"文件不存在，尝试创建: {file_path}")
                    if not self.create_mmap_file(file_type, shard_id):
                        return False
                
                # 检查文件权限
                if not os.access(file_path, os.R_OK):
                    self.logger.error(f"文件无读取权限: {file_path}")
                    return False
                
                # 对于写入模式，检查写入权限
                if mode == MmapAccessMode.READ_WRITE and not os.access(file_path, os.W_OK):
                    self.logger.error(f"文件无写入权限: {file_path}")
                    # 尝试修改文件权限
                    try:
                        import stat
                        os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
                        self.logger.info(f"已修改文件权限: {file_path}")
                    except Exception as e:
                        self.logger.error(f"无法修改文件权限: {file_path} - {e}")
                        return False
                
                # 打开文件
                file_mode = mode.value
                self.logger.debug(f"尝试打开文件: {file_path} (模式: {file_mode})")
                
                # 在Windows上，需要保持文件句柄打开
                f = open(file_path, file_mode + 'b')
                
                # 读取头部
                header_data = f.read(MmapHeader.HEADER_SIZE)
                if len(header_data) < MmapHeader.HEADER_SIZE:
                    f.close()
                    raise ValueError(f"文件头部不完整: {file_path}")
                
                self.logger.debug(f"头部数据长度: {len(header_data)}")
                header = MmapHeader.unpack(header_data)
                self.logger.debug(f"解析的头部: magic={header.magic}, version={header.version}, index_offset={header.index_offset}")
                
                if not self._validate_header(header):
                    f.close()
                    return False
                
                # 创建内存映射（适配Windows）
                try:
                    # 根据文件模式正确设置mmap访问权限
                    if mode == MmapAccessMode.READ_WRITE or 'w' in file_mode or '+' in file_mode:
                        mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE)
                        self.logger.debug(f"MMap创建成功 (写入模式): {file_path}")
                    else:
                        mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                        self.logger.debug(f"MMap创建成功 (只读模式): {file_path}")
                except Exception as mmap_error:
                    self.logger.error(f"MMap创建失败: {mmap_error}")
                    f.close()
                    
                    # 尝试重新打开文件并创建mmap
                    try:
                        # 如果原来是只读模式且失败了，尝试读写模式
                        if mode == MmapAccessMode.READ_WRITE and file_mode == 'r':
                            self.logger.info(f"尝试以读写模式重新打开文件: {file_path}")
                            f = open(file_path, 'r+b')
                            mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE)
                        else:
                            f = open(file_path, file_mode + 'b')
                            if mode == MmapAccessMode.READ_WRITE:
                                mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE)
                            else:
                                mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                        self.logger.debug(f"使用修正模式创建MMap: {file_path}")
                    except Exception as retry_error:
                        self.logger.error(f"重试创建MMap失败: {retry_error}")
                        return False
                
                # 加载索引
                index = self._load_index(mmap_obj, header)
                
                # 保存到映射表
                self.logger.debug(f"保存到映射表: {file_path}")
                self.file_mappings[file_path] = mmap_obj
                self.file_headers[file_path] = header
                self.file_indexes[file_path] = index
                
                # 调试：验证存储的头部信息
                self.logger.debug(f"存储头部信息: index_offset={header.index_offset}, data_area_size={header.index_offset - MmapHeader.HEADER_SIZE}")
                # 保存文件句柄（在Windows上需要保持打开）
                if not hasattr(self, '_file_handles'):
                    self._file_handles = {}
                self._file_handles[file_path] = f
                self.logger.debug(f"映射表保存完成: {file_path}")
                
                self.stats['files_opened'] += 1
                self.logger.info(f"打开mmap文件: {file_path}")
                return True
                
            except Exception as e:
                self.stats['errors'] += 1
                self.logger.error(f"打开mmap文件失败: {file_path} - {e}")
                return False
    
    def _load_index(self, mmap_obj: mmap.mmap, header: MmapHeader) -> Dict[bytes, MmapIndexEntry]:
        """加载索引"""
        index = {}
        
        if header.index_size == 0:
            self.logger.debug("索引大小为0，返回空索引")
            return index
        
        try:
            self.logger.debug(f"开始加载索引: offset={header.index_offset}, size={header.index_size}")
            # 读取索引数据
            mmap_obj.seek(header.index_offset)
            index_data = mmap_obj.read(header.index_size)
            self.logger.debug(f"读取索引数据: {len(index_data)} 字节")
            
            # 解析索引条目
            offset = 0
            while offset < len(index_data):
                if offset + MmapIndexEntry.ENTRY_SIZE > len(index_data):
                    break
                
                entry_data = index_data[offset:offset + MmapIndexEntry.ENTRY_SIZE]
                entry = MmapIndexEntry.unpack(entry_data)
                index[entry.key_hash] = entry
                offset += MmapIndexEntry.ENTRY_SIZE
            
            self.logger.debug(f"成功加载 {len(index)} 个索引条目")
                
        except Exception as e:
            self.logger.error(f"加载索引失败: {e}")
        
        return index
    
    def _save_index(self, file_path: str) -> bool:
        """保存索引"""
        try:
            mmap_obj = self.file_mappings[file_path]
            header = self.file_headers[file_path]
            index = self.file_indexes[file_path]
            
            # 序列化索引
            index_data = b''
            for entry in index.values():
                index_data += entry.pack()
            
            # 更新头部
            header.index_size = len(index_data)
            header.last_modified = time.time()
            
            # 写入头部
            mmap_obj.seek(0)
            mmap_obj.write(header.pack())
            
            # 写入索引
            mmap_obj.seek(header.index_offset)
            mmap_obj.write(index_data)
            
            # 同步到磁盘
            mmap_obj.flush()
            
            return True
            
        except Exception as e:
            self.logger.error(f"保存索引失败: {file_path} - {e}")
            return False
    
    def _align_size(self, size: int) -> int:
        """内存对齐"""
        page_size = 4096
        return (size + page_size - 1) // page_size * page_size
    
    def _calculate_key_hash(self, key: str) -> bytes:
        """计算键的哈希值"""
        return hashlib.md5(key.encode('utf-8')).digest()
    
    def _expand_file_if_needed(self, file_path: str, required_size: int) -> bool:
        """如果需要则扩展文件 - 多进程安全版本"""
        try:
            # 检查文件是否已打开
            if file_path not in self.file_mappings:
                self.logger.warning(f"文件未打开，无法扩展: {file_path}")
                return False
                
            mmap_obj = self.file_mappings[file_path]
            header = self.file_headers[file_path]
            
            current_size = len(mmap_obj)
            if current_size >= required_size:
                return True
            
            # 计算新大小
            new_size = max(current_size * self.growth_factor, required_size + 128000000)  # 额外128MB缓冲
            new_size = min(new_size, self.max_file_size)
            new_size = self._align_size(int(new_size))
            
            if new_size <= current_size:
                self.logger.error(f"无法扩展文件: {file_path} (当前: {current_size}, 需要: {required_size})")
                return False
            
            self.logger.info(f"开始扩展文件: {file_path} ({current_size} -> {new_size})")
            
            # 多进程安全：使用Windows兼容的文件锁防止并发扩展
            lock_file_path = file_path + ".expand_lock"
            try:
                # Windows兼容的文件锁实现
                import platform
                if platform.system() == 'Windows':
                    try:
                        # 使用独占模式创建锁文件
                        with open(lock_file_path, 'x') as lock_file:
                            lock_file.write(f"{os.getpid()}:{time.time()}")
                    except FileExistsError:
                        # 文件已存在，检查是否超时
                        try:
                            if os.path.exists(lock_file_path):
                                file_age = time.time() - os.path.getmtime(lock_file_path)
                                if file_age > 30:  # 30秒超时
                                    os.remove(lock_file_path)
                                    with open(lock_file_path, 'x') as lock_file:
                                        lock_file.write(f"{os.getpid()}:{time.time()}")
                                else:
                                    raise IOError("Lock held by another process")
                        except:
                            raise IOError("Lock held by another process")
                else:
                    # Unix系统使用fcntl
                    import fcntl
                    with open(lock_file_path, 'w') as lock_file:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                
                # 再次检查当前文件大小（可能其他进程已经扩展）
                with open(file_path, 'rb') as check_f:
                    check_f.seek(0, 2)  # 移动到文件末尾
                    actual_size = check_f.tell()
                    if actual_size >= required_size:
                        self.logger.info(f"文件已被其他进程扩展: {file_path} ({actual_size})")
                        # 重新映射到新大小
                        return self._remap_file(file_path)
                
                # 执行文件扩展
                return self._perform_file_expansion(file_path, new_size, required_size)
                        
            except IOError:
                # 其他进程正在扩展，等待并重新映射
                self.logger.info(f"等待其他进程完成文件扩展: {file_path}")
                time.sleep(0.1)  # 等待100ms
                return self._remap_file(file_path)
                        
            finally:
                # 清理锁文件
                try:
                    if os.path.exists(lock_file_path):
                        os.remove(lock_file_path)
                except:
                    pass
                    
        except Exception as e:
            self.logger.error(f"扩展文件失败: {file_path} - {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def _perform_file_expansion(self, file_path: str, new_size: int, required_size: int) -> bool:
        """执行实际的文件扩展操作"""
        try:
            mmap_obj = self.file_mappings[file_path]
            header = self.file_headers[file_path]
            
            # 关闭当前映射和文件句柄
            mmap_obj.close()
            if hasattr(self, '_file_handles') and file_path in self._file_handles:
                self._file_handles[file_path].close()
            
            # 扩展文件
            with open(file_path, 'r+b') as f:
                f.truncate(new_size)
            
            # 重新打开文件句柄和映射
            f = open(file_path, 'r+b')
            new_mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE)
            
            # 更新映射表和文件句柄
            self.file_mappings[file_path] = new_mmap_obj
            if not hasattr(self, '_file_handles'):
                self._file_handles = {}
            self._file_handles[file_path] = f
            
            # 更新头部中的索引偏移量
            index_reserved_size = 64000  # 64KB索引预留空间
            new_index_offset = new_size - index_reserved_size
            old_index_offset = header.index_offset
            header.index_offset = new_index_offset
            
            # 更新文件头部表中的引用
            self.file_headers[file_path] = header
            
            # 重新加载索引
            self._load_index(file_path)
            
            # 保存更新后的头部
            new_mmap_obj.seek(0)
            new_mmap_obj.write(header.pack())
            new_mmap_obj.flush()
            
            self.logger.info(f"成功扩展文件: {file_path} (索引偏移: {old_index_offset} -> {new_index_offset})")
            
            return True
            
        except Exception as e:
            self.logger.error(f"执行文件扩展失败: {file_path} - {e}")
            return False
    
    def _remap_file(self, file_path: str) -> bool:
        """重新映射文件到新大小"""
        try:
            # 关闭当前映射
            if file_path in self.file_mappings:
                self.file_mappings[file_path].close()
            if hasattr(self, '_file_handles') and file_path in self._file_handles:
                self._file_handles[file_path].close()
            
            # 重新打开文件
            f = open(file_path, 'r+b')
            new_mmap_obj = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE)
            
            # 更新映射表
            self.file_mappings[file_path] = new_mmap_obj
            if not hasattr(self, '_file_handles'):
                self._file_handles = {}
            self._file_handles[file_path] = f
            
            # 重新加载头部和索引
            header_data = new_mmap_obj[:MmapHeader.HEADER_SIZE]
            header = MmapHeader.unpack(header_data)
            self.file_headers[file_path] = header
            
            # 重新加载索引
            self._load_index(file_path)
            
            self.logger.info(f"成功重新映射文件: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"重新映射文件失败: {file_path} - {e}")
            return False
    
    def _reload_file_mappings(self, file_path: str) -> bool:
        """重新加载文件映射、头部和索引"""
        try:
            if file_path not in self.file_mappings:
                return False
                
            # 重新加载头部
            mmap_obj = self.file_mappings[file_path]
            header_data = mmap_obj[:MmapHeader.HEADER_SIZE]
            header = MmapHeader.unpack(header_data)
            self.file_headers[file_path] = header
            
            # 重新加载索引
            self._load_index(file_path)
            
            return True
            
        except Exception as e:
            self.logger.error(f"重新加载文件映射失败: {file_path} - {e}")
            return False
    
    def read_data(self, file_type: MmapFileType, shard_id: int, key: str) -> Optional[bytes]:
        """读取数据"""
        file_path = self._get_file_path(file_type, shard_id)
        file_lock = self._get_file_lock(file_path)
        
        with file_lock:
            try:
                if file_path not in self.file_mappings:
                    if not self.open_mmap_file(file_type, shard_id, MmapAccessMode.READ_ONLY):
                        return None
                
                mmap_obj = self.file_mappings[file_path]
                index = self.file_indexes[file_path]
                
                key_hash = self._calculate_key_hash(key)
                if key_hash not in index:
                    self.stats['cache_misses'] += 1
                    return None
                
                entry = index[key_hash]
                
                # 读取数据
                mmap_obj.seek(entry.data_offset)
                data = mmap_obj.read(entry.data_size)
                
                self.stats['read_operations'] += 1
                self.stats['cache_hits'] += 1
                
                return data
                
            except Exception as e:
                self.stats['errors'] += 1
                self.logger.error(f"读取数据失败: {key} - {e}")
                return None
    
    def write_data(self, file_type: MmapFileType, shard_id: int, key: str, 
                   data: bytes) -> bool:
        """写入数据"""
        file_path = self._get_file_path(file_type, shard_id)
        file_lock = self._get_file_lock(file_path)
        
        with file_lock:
            try:
                if file_path not in self.file_mappings:
                    if not self.open_mmap_file(file_type, shard_id, MmapAccessMode.READ_WRITE):
                        return False
                
                mmap_obj = self.file_mappings[file_path]
                header = self.file_headers[file_path]
                index = self.file_indexes[file_path]
                
                # 调试：输出当前头部信息
                self.logger.debug(f"写入数据开始: file={file_path}, index_offset={header.index_offset}, data_area_size={header.index_offset - MmapHeader.HEADER_SIZE}")
                
                # 紧急修复：如果index_offset异常小，重新初始化文件
                if header.index_offset <= MmapHeader.HEADER_SIZE:
                    self.logger.error(f"检测到异常的index_offset: {header.index_offset}, 重新初始化文件")
                    mmap_obj.close()
                    if hasattr(self, '_file_handles') and file_path in self._file_handles:
                        self._file_handles[file_path].close()
                        del self._file_handles[file_path]
                    del self.file_mappings[file_path]
                    del self.file_headers[file_path]
                    del self.file_indexes[file_path]
                    
                    # 删除并重新创建文件
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    
                    if not self.create_mmap_file(file_type, shard_id, self.initial_file_size):
                        return False
                    
                    if not self.open_mmap_file(file_type, shard_id, MmapAccessMode.READ_WRITE):
                        return False
                    
                    # 重新获取对象
                    mmap_obj = self.file_mappings[file_path]
                    header = self.file_headers[file_path]
                    index = self.file_indexes[file_path]
                    self.logger.info(f"文件重新初始化完成: index_offset={header.index_offset}")
                
                key_hash = self._calculate_key_hash(key)
                
                # 计算数据偏移
                if key_hash in index:
                    # 更新现有数据
                    entry = index[key_hash]
                    data_offset = entry.data_offset
                else:
                    # 分配新空间 - 数据应该在数据区域内，不能超过索引区域
                    # 数据区域: HEADER_SIZE 到 index_offset
                    data_area_start = MmapHeader.HEADER_SIZE
                    data_area_end = header.index_offset
                    
                    # 找到当前数据区域中的最大偏移
                    max_data_end = data_area_start
                    for existing_entry in index.values():
                        if existing_entry.data_offset >= data_area_start and existing_entry.data_offset < data_area_end:
                            data_end = existing_entry.data_offset + existing_entry.data_size
                            max_data_end = max(max_data_end, data_end)
                    
                    data_offset = max_data_end
                    
                # 检查是否有足够空间，包含安全边界
                safety_margin = 1024  # 1KB安全边界
                if data_offset + len(data) + safety_margin > data_area_end:
                    self.logger.debug(f"数据区域空间不足: 需要{data_offset + len(data) + safety_margin}, 可用{data_area_end}")
                    self.logger.debug(f"空间计算详情: data_offset={data_offset}, data_size={len(data)}, data_area_start={data_area_start}, data_area_end={data_area_end}, header.index_offset={header.index_offset}")
                    
                    # 尝试扩展文件
                    required_size = data_offset + len(data) + 64000  # 额外64KB缓冲空间
                    self.logger.info(f"尝试扩展文件以满足空间需求: {required_size}")
                    if self._expand_file_if_needed(file_path, required_size):
                        # 重新加载映射对象、头部信息和索引
                        if not self._reload_file_mappings(file_path):
                            self.logger.error(f"重新加载文件映射失败: {file_path}")
                            return False
                            
                        mmap_obj = self.file_mappings[file_path]
                        header = self.file_headers[file_path]
                        index = self.file_indexes[file_path]
                        
                        # 更新数据区域边界
                        data_area_start = MmapHeader.HEADER_SIZE
                        data_area_end = header.index_offset
                        
                        self.logger.info(f"文件扩展后新的数据区域: {data_area_start} -> {data_area_end}")
                        
                        # 重新计算数据偏移量
                        if index:
                            max_data_end = max((entry.data_offset + entry.data_size for entry in index.values()), default=data_area_start)
                            data_offset = max_data_end
                        else:
                            data_offset = data_area_start
                        
                        # 重新验证空间
                        if data_offset + len(data) + safety_margin > data_area_end:
                            self.logger.error(f"扩展后仍然空间不足: 需要{data_offset + len(data) + safety_margin}, 可用{data_area_end}")
                            return False
                    else:
                        self.logger.error(f"文件扩展失败")
                        return False
                    return False
                
                # 重新获取映射对象、头部和索引（可能已更新）
                mmap_obj = self.file_mappings[file_path]
                header = self.file_headers[file_path]
                index = self.file_indexes[file_path]
                
                # 写入数据
                try:
                    mmap_obj.seek(data_offset)
                    mmap_obj.write(data)
                    self.logger.debug(f"数据写入成功: {key} (偏移: {data_offset}, 大小: {len(data)})")
                except Exception as write_error:
                    self.logger.error(f"写入数据失败: {key} - {write_error}")
                    # 如果是只读错误，尝试重新打开文件
                    if "readonly" in str(write_error).lower():
                        self.logger.info(f"检测到只读错误，尝试重新打开文件: {file_path}")
                        # 关闭当前映射
                        if file_path in self.file_mappings:
                            try:
                                self.file_mappings[file_path].close()
                                if hasattr(self, '_file_handles') and file_path in self._file_handles:
                                    self._file_handles[file_path].close()
                            except:
                                pass
                            del self.file_mappings[file_path]
                            del self.file_headers[file_path]
                            del self.file_indexes[file_path]
                        
                        # 重新打开并重试
                        if self.open_mmap_file(file_type, shard_id, MmapAccessMode.READ_WRITE):
                            mmap_obj = self.file_mappings[file_path]
                            try:
                                mmap_obj.seek(data_offset)
                                mmap_obj.write(data)
                                self.logger.info(f"重新打开后写入成功: {key}")
                            except Exception as retry_error:
                                self.logger.error(f"重新打开后写入仍然失败: {key} - {retry_error}")
                                return False
                        else:
                            return False
                    else:
                        return False
                
                # 更新索引
                entry = MmapIndexEntry(
                    key_hash=key_hash,
                    data_offset=data_offset,
                    data_size=len(data),
                    timestamp=time.time(),
                    flags=0
                )
                index[key_hash] = entry
                
                # 更新头部
                header.data_size = max(header.data_size, data_offset + len(data))
                header.last_modified = time.time()
                
                # 保存索引
                if not self._save_index(file_path):
                    return False
                
                self.stats['write_operations'] += 1
                return True
                
            except Exception as e:
                self.stats['errors'] += 1
                self.logger.error(f"写入数据失败: {key} - {e}")
                return False
    
    def delete_data(self, file_type: MmapFileType, shard_id: int, key: str) -> bool:
        """删除数据"""
        file_path = self._get_file_path(file_type, shard_id)
        file_lock = self._get_file_lock(file_path)
        
        with file_lock:
            try:
                if file_path not in self.file_mappings:
                    return False
                
                index = self.file_indexes[file_path]
                key_hash = self._calculate_key_hash(key)
                
                if key_hash not in index:
                    return False
                
                # 从索引中删除
                del index[key_hash]
                
                # 保存索引
                if not self._save_index(file_path):
                    return False
                
                return True
                
            except Exception as e:
                self.stats['errors'] += 1
                self.logger.error(f"删除数据失败: {key} - {e}")
                return False
    
    def close_file(self, file_type: MmapFileType, shard_id: int = 0) -> bool:
        """关闭文件"""
        file_path = self._get_file_path(file_type, shard_id)
        file_lock = self._get_file_lock(file_path)
        
        with file_lock:
            try:
                if file_path not in self.file_mappings:
                    return True
                
                # 保存索引
                self._save_index(file_path)
                
                # 关闭映射
                mmap_obj = self.file_mappings[file_path]
                mmap_obj.close()
                
                # 清理映射表
                del self.file_mappings[file_path]
                del self.file_headers[file_path]
                del self.file_indexes[file_path]
                
                # 关闭文件句柄（在Windows上需要保持打开）
                if hasattr(self, '_file_handles') and file_path in self._file_handles:
                    self._file_handles[file_path].close()
                    del self._file_handles[file_path]
                
                self.logger.info(f"关闭mmap文件: {file_path}")
                return True
                
            except Exception as e:
                self.logger.error(f"关闭文件失败: {file_path} - {e}")
                return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            stats = self.stats.copy()
            
            # 添加文件统计
            stats['open_files'] = len(self.file_mappings)
            stats['total_index_entries'] = sum(len(index) for index in self.file_indexes.values())
            
            return stats
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态信息（兼容性方法）"""
        return self.get_statistics()
    
    def cleanup(self) -> None:
        """清理资源"""
        with self._lock:
            # 关闭所有文件
            for file_path in list(self.file_mappings.keys()):
                try:
                    mmap_obj = self.file_mappings[file_path]
                    mmap_obj.close()
                except Exception as e:
                    self.logger.error(f"清理文件失败: {file_path} - {e}")
            
            # 清理映射表
            self.file_mappings.clear()
            self.file_headers.clear()
            self.file_indexes.clear()
            
            # 关闭所有文件句柄
            if hasattr(self, '_file_handles'):
                for f in list(self._file_handles.values()):
                    try:
                        f.close()
                    except Exception as e:
                        self.logger.error(f"清理文件句柄失败: {f.name} - {e}")
                self._file_handles.clear()
            
            self.logger.info("高并发mmap管理器清理完成")

    def get_all_keys(self, file_type: MmapFileType, shard_id: int = 0) -> List[str]:
        """获取指定分片的所有键"""
        try:
            file_path = self._get_file_path(file_type, shard_id)
            
            # 检查文件是否存在
            if not os.path.exists(file_path):
                return []
            
            # 获取映射表
            mapping_key = f"{file_type.value}_{shard_id}"
            if mapping_key not in self.file_indexes:
                # 尝试打开文件以加载映射表
                if not self.open_mmap_file(file_type, shard_id, MmapAccessMode.READ_ONLY):
                    return []
            
            # 从映射表获取所有键
            if mapping_key in self.file_indexes:
                mapping = self.file_indexes[mapping_key]
                if 'index' in mapping:
                    return list(mapping['index'].keys())
            
            return []
            
        except Exception as e:
            self.logger.error(f"获取所有键失败: {file_type.value}_{shard_id} - {e}")
            return []
    
    def get_file_size(self, file_type: MmapFileType, shard_id: int = 0) -> int:
        """获取文件大小"""
        file_path = self._get_file_path(file_type, shard_id)
        file_lock = self._get_file_lock(file_path)
        
        with file_lock:
            if file_path not in self.file_mappings:
                if not self.open_mmap_file(file_type, shard_id, MmapAccessMode.READ_ONLY):
                    return 0
            
            mmap_obj = self.file_mappings[file_path]
            return len(mmap_obj)


# 全局mmap管理器实例
_global_mmap_manager: Optional[HighConcurrencyMmapManager] = None
_global_mmap_manager_lock = threading.Lock()


def get_global_mmap_manager(project_root: str) -> HighConcurrencyMmapManager:
    """获取全局mmap管理器实例"""
    global _global_mmap_manager
    
    if _global_mmap_manager is None:
        with _global_mmap_manager_lock:
            if _global_mmap_manager is None:
                _global_mmap_manager = HighConcurrencyMmapManager(project_root)
    
    return _global_mmap_manager


def init_global_mmap_manager(project_root: str) -> HighConcurrencyMmapManager:
    """初始化全局mmap管理器"""
    global _global_mmap_manager
    
    with _global_mmap_manager_lock:
        if _global_mmap_manager is not None:
            _global_mmap_manager.cleanup()
        
        _global_mmap_manager = HighConcurrencyMmapManager(project_root)
        return _global_mmap_manager


def shutdown_global_mmap_manager() -> None:
    """关闭全局mmap管理器"""
    global _global_mmap_manager
    
    with _global_mmap_manager_lock:
        if _global_mmap_manager is not None:
            _global_mmap_manager.cleanup()
            _global_mmap_manager = None
