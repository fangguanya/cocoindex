#ifndef TEMPLATE_CONTAINER_H
#define TEMPLATE_CONTAINER_H

#include <vector>
#include <iostream>
#include <stdexcept>

// 模板类：通用容器
template<typename T>
class Container {
private:
    std::vector<T> items;
    
public:
    Container();
    virtual ~Container();
    
    void add(const T& item);
    T get(size_t index) const;
    size_t size() const;
    bool empty() const;
    void clear();
    
    // 模板成员函数
    template<typename Predicate>
    void removeIf(Predicate pred);
    
    // 虚函数，可以被继承
    virtual void print() const;
};

// 模板类：键值对
template<typename K, typename V>
class Pair {
private:
    K key;
    V value;
    
public:
    Pair(const K& k, const V& v);
    
    K getKey() const;
    V getValue() const;
    void setKey(const K& k);
    void setValue(const V& v);
    
    // 操作符重载
    bool operator==(const Pair<K, V>& other) const;
    bool operator!=(const Pair<K, V>& other) const;
};

// 模板继承：栈继承自容器
template<typename T>
class Stack : public Container<T> {
public:
    Stack();
    
    void push(const T& item);
    T pop();
    T top() const;
    
    // 重写父类虚函数
    void print() const override;
};

// 模板特化示例
template<>
class Container<std::string> {
public:
    Container();
    ~Container();
    void add(std::string item);
    std::string get() const;

    // 成员函数模板
    template<typename Archive>
    void serialize(Archive& ar) {
        ar & data;
    }

private:
    std::string data;
};

// 类模板的偏特化
template<typename T>
class Pair<T, int> {
public:
    Pair(T first, int second) : first_val(first), second_val(second) {
        std::cout << "Using partial specialization for Pair<T, int>" << std::endl;
    }

private:
    T first_val;
    int second_val;
};

#endif // TEMPLATE_CONTAINER_H 