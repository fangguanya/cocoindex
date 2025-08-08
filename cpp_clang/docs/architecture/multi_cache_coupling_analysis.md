# 多缓存耦合分析报告

## 1. 当前缓存架构概述

### 1.1 缓存组件
- **SharedClassCache**: 类解析结果缓存
- **SharedHeaderManager**: 头文件处理状态缓存  
- **DistributedFileManager**: 文件ID和路径映射缓存

### 1.2 数据流向图
```
DistributedFileManager (基础服务)
    ↓ 提供文件映射
SharedHeaderManager (头文件处理)
    ↓ 提供头文件状态
SharedClassCache (类解析)
    ↓ 提供类信息
EntityExtractor (实体提取器)
```

## 2. 依赖关系分析

### 2.1 SharedClassCache 依赖分析

#### 直接依赖
- **DistributedFileManager**: 
  - 用途：获取文件ID映射
  - 调用频率：高（每次类解析都需要）
  - 耦合度：强依赖

- **SharedHeaderManager**:
  - 用途：获取头文件处理状态
  - 调用频率：中等（解析头文件中的类时）
  - 耦合度：中等依赖

#### 数据共享
- 类解析结果可能被多个缓存引用
- 模板类型解析结果需要跨缓存同步

### 2.2 SharedHeaderManager 依赖分析

#### 直接依赖
- **DistributedFileManager**:
  - 用途：获取文件路径映射
  - 调用频率：高（每次头文件处理都需要）
  - 耦合度：强依赖

#### 独立性
- 头文件处理相对独立
- 不依赖其他缓存的数据

### 2.3 DistributedFileManager 依赖分析

#### 被依赖关系
- 被 SharedClassCache 依赖
- 被 SharedHeaderManager 依赖
- 作为基础服务被上层缓存使用

#### 独立性
- 完全独立，不依赖其他缓存
- 提供核心的文件映射服务

## 3. 性能瓶颈分析

### 3.1 锁竞争问题
```
当前架构：
┌─────────────────┐
│  全局文件锁     │ ← 所有缓存共享同一个锁
├─────────────────┤
│ SharedClassCache│
│ SharedHeaderMgr │
│ DistributedFile │
└─────────────────┘
```

**问题**：
- 192个进程竞争同一个文件锁
- 锁等待时间随进程数线性增长
- 缓存间相互阻塞

### 3.2 数据同步问题
- 缓存间数据更新需要同步
- 文件I/O操作频繁
- 序列化/反序列化开销大

### 3.3 内存使用问题
- 每个进程独立加载缓存数据
- 内存使用随进程数线性增长
- 数据重复存储

## 4. 解耦策略设计

### 4.1 策略1：统一缓存管理接口

#### 设计目标
- 统一管理所有缓存实例
- 提供标准化的缓存接口
- 实现缓存间松耦合

#### 架构设计
```python
class UnifiedCacheManager:
    def __init__(self):
        self.caches = {}  # 缓存实例注册表
        self.dependencies = {}  # 依赖关系图
        self.event_bus = CacheEventBus()  # 事件总线
    
    def register_cache(self, cache_name, cache_instance, dependencies=None):
        """注册缓存实例"""
        pass
    
    def get_cache(self, cache_name):
        """获取缓存实例"""
        pass
    
    def sync_dependencies(self, cache_name):
        """同步依赖缓存的数据"""
        pass
```

### 4.2 策略2：事件驱动数据同步

#### 设计目标
- 通过事件总线实现缓存间通信
- 减少直接依赖
- 支持异步数据同步

#### 架构设计
```python
class CacheEventBus:
    def __init__(self):
        self.subscribers = {}
    
    def subscribe(self, event_type, callback):
        """订阅缓存事件"""
        pass
    
    def publish(self, event_type, data):
        """发布缓存事件"""
        pass

# 事件类型定义
CACHE_EVENTS = {
    'class_resolved': '类解析完成',
    'header_processed': '头文件处理完成',
    'file_mapped': '文件映射更新',
    'template_resolved': '模板解析完成'
}
```

### 4.3 策略3：分片级别的数据隔离

#### 设计目标
- 每个缓存使用独立的分片
- 减少缓存间的锁竞争
- 支持缓存独立扩容

#### 分片策略
```python
class ShardedCacheManager:
    def __init__(self, num_shards=64):
        self.num_shards = num_shards
        self.shards = {}  # 分片实例
    
    def get_shard(self, key):
        """根据key获取对应的分片"""
        shard_id = hash(key) % self.num_shards
        return self.shards[shard_id]
    
    def get_cache_for_type(self, cache_type, key):
        """获取指定类型的缓存分片"""
        return self.get_shard(f"{cache_type}:{key}")
```

## 5. 高并发优化策略

### 5.1 分片数量优化

#### 当前建议
- **分片数量**: 64-256个分片
- **分片策略**: 基于key哈希值 + 缓存类型
- **负载均衡**: 动态分片分配

#### 分片算法
```python
def calculate_shard_id(cache_type: str, key: str) -> int:
    """计算分片ID"""
    # 组合缓存类型和key进行哈希
    combined_key = f"{cache_type}:{key}"
    return hash(combined_key) % NUM_SHARDS

# 分片分配示例
SHARD_ALLOCATION = {
    'class_cache': range(0, 32),      # 0-31
    'header_cache': range(32, 48),    # 32-47  
    'file_cache': range(48, 64)       # 48-63
}
```

### 5.2 锁机制优化

#### 读写锁分离
```python
class ShardedReadWriteLock:
    def __init__(self):
        self.read_lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.readers = 0
    
    def acquire_read(self):
        """获取读锁"""
        with self.read_lock:
            self.readers += 1
            if self.readers == 1:
                self.write_lock.acquire()
    
    def release_read(self):
        """释放读锁"""
        with self.read_lock:
            self.readers -= 1
            if self.readers == 0:
                self.write_lock.release()
    
    def acquire_write(self):
        """获取写锁"""
        self.write_lock.acquire()
    
    def release_write(self):
        """释放写锁"""
        self.write_lock.release()
```

#### 锁粒度优化
- 分片级别锁，减少竞争
- 锁超时机制，避免死锁
- 锁统计和监控

### 5.3 内存映射优化

#### 大页内存
```python
import mmap

def create_large_page_mmap(file_path: str, size: int):
    """创建大页内存映射"""
    # 使用大页内存减少TLB miss
    flags = mmap.MAP_SHARED
    if hasattr(mmap, 'MAP_HUGETLB'):
        flags |= mmap.MAP_HUGETLB
    
    with open(file_path, 'r+b') as f:
        return mmap.mmap(f.fileno(), size, flags=flags)
```

#### 预分配和内存对齐
```python
class PreallocatedMmapManager:
    def __init__(self, initial_size: int, growth_factor: float = 2.0):
        self.initial_size = initial_size
        self.growth_factor = growth_factor
        self.current_size = initial_size
    
    def allocate_mmap(self, file_path: str):
        """预分配内存映射"""
        # 预分配内存减少动态分配
        size = self._calculate_optimal_size()
        return create_large_page_mmap(file_path, size)
    
    def _calculate_optimal_size(self) -> int:
        """计算最优大小（内存对齐）"""
        # 按4KB页面对齐
        page_size = 4096
        return (self.current_size + page_size - 1) // page_size * page_size
```

## 6. 实施优先级

### 6.1 高优先级（立即实施）
1. **统一缓存管理接口** - 基础架构
2. **分片锁机制** - 解决锁竞争
3. **本地缓存优化** - 快速性能提升

### 6.2 中优先级（1-2周内）
1. **事件驱动同步** - 解耦缓存
2. **内存映射优化** - 提升I/O性能
3. **分片负载均衡** - 优化资源使用

### 6.3 低优先级（2-4周内）
1. **监控和告警** - 运维支持
2. **性能调优** - 精细优化
3. **文档完善** - 知识沉淀

## 7. 风险评估

### 7.1 技术风险
- **复杂度风险**: 分片机制增加系统复杂度
- **兼容性风险**: 现有代码需要适配新接口
- **性能风险**: 初期可能性能不如预期

### 7.2 缓解措施
- **渐进式实施**: 分阶段部署，降低风险
- **充分测试**: 每个阶段都要充分测试
- **回滚方案**: 准备快速回滚机制

## 8. 成功指标

### 8.1 性能指标
- **锁等待时间**: 减少90%以上
- **内存使用**: 减少50%以上
- **并发度**: 支持192个进程

### 8.2 质量指标
- **可用性**: 99.9%以上
- **数据一致性**: 100%
- **错误率**: 0.1%以下

## 9. 结论

通过多缓存耦合分析，我们识别了当前架构的主要问题：
1. **锁竞争严重** - 需要分片锁机制
2. **缓存间强耦合** - 需要事件驱动解耦
3. **内存使用效率低** - 需要内存映射优化

建议按照优先级逐步实施优化方案，预期可以实现5-10倍的性能提升，并支持192个进程并发。
