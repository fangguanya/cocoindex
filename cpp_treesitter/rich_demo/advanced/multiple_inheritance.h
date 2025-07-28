#ifndef MULTIPLE_INHERITANCE_H
#define MULTIPLE_INHERITANCE_H

#include <string>
#include <iostream>
#include <vector>

// 接口1：可打印的
class Printable {
public:
    virtual ~Printable() = default;
    virtual void print() const = 0;
    virtual std::string getPrintFormat() const = 0;
};

// 接口2：可序列化的
class Serializable {
public:
    virtual ~Serializable() = default;
    virtual std::string serialize() const = 0;
    virtual void deserialize(const std::string& data) = 0;
    virtual std::string getSerializationFormat() const = 0;
};

// 接口3：可存储的
class Storable {
public:
    virtual ~Storable() = default;
    virtual bool save(const std::string& filename) const = 0;
    virtual bool load(const std::string& filename) = 0;
    virtual size_t getStorageSize() const = 0;
};

// 基础类：可识别的
class Identifiable {
protected:
    std::string id;
    std::string name;

public:
    Identifiable(const std::string& id, const std::string& name);
    virtual ~Identifiable() = default;
    
    std::string getId() const;
    std::string getName() const;
    void setName(const std::string& newName);
    
    virtual std::string getType() const = 0;
};

// 多重继承示例1：文档类（继承自Identifiable + 实现Printable和Serializable）
class Document : public Identifiable, public Printable, public Serializable {
private:
    std::string content;
    std::string format;

public:
    Document(const std::string& id, const std::string& name, const std::string& content);
    
    // 实现Identifiable的虚函数
    std::string getType() const override;
    
    // 实现Printable接口
    void print() const override;
    std::string getPrintFormat() const override;
    
    // 实现Serializable接口
    std::string serialize() const override;
    void deserialize(const std::string& data) override;
    std::string getSerializationFormat() const override;
    
    // Document特有方法
    std::string getContent() const;
    void setContent(const std::string& newContent);
    void setFormat(const std::string& newFormat);
};

// 多重继承示例2：多媒体文件（继承Document + 实现Storable）
class MultimediaFile : public Document, public Storable {
private:
    size_t fileSize;
    std::string mediaType;
    std::vector<std::string> metadata;

public:
    MultimediaFile(const std::string& id, const std::string& name, 
                   const std::string& content, const std::string& mediaType);
    
    // 重写父类方法
    std::string getType() const override;
    void print() const override;
    std::string serialize() const override;
    
    // 实现Storable接口
    bool save(const std::string& filename) const override;
    bool load(const std::string& filename) override;
    size_t getStorageSize() const override;
    
    // MultimediaFile特有方法
    void addMetadata(const std::string& metadata);
    std::vector<std::string> getMetadata() const;
    std::string getMediaType() const;
    void setFileSize(size_t size);
};

// 虚继承示例：避免钻石问题
class Base {
protected:
    int baseValue;

public:
    Base(int value);
    virtual ~Base() = default;
    virtual void showBase() const;
    int getBaseValue() const;
};

class Left : public virtual Base {
protected:
    int leftValue;

public:
    Left(int baseVal, int leftVal);
    virtual void showLeft() const;
    int getLeftValue() const;
};

class Right : public virtual Base {
protected:
    int rightValue;

public:
    Right(int baseVal, int rightVal);
    virtual void showRight() const;
    int getRightValue() const;
};

// 钻石继承：同时继承Left和Right（它们都虚继承自Base）
class Diamond : public Left, public Right {
private:
    int diamondValue;

public:
    Diamond(int baseVal, int leftVal, int rightVal, int diamondVal);
    
    // 重写虚函数
    void showBase() const override;
    void showLeft() const override;
    void showRight() const override;
    
    // Diamond特有方法
    void showDiamond() const;
    void showAll() const;
    int getDiamondValue() const;
};

#endif // MULTIPLE_INHERITANCE_H 