#ifndef VALIDATION_ADVANCED_TEMPLATES_H
#define VALIDATION_ADVANCED_TEMPLATES_H

#include <iostream>
#include <string>
#include <vector>
#include <memory>
#include <array>

namespace Validation {

// 1. 基础模板函数 - 用于测试模板参数导出
template<typename T>
T add_values(T a, T b) {
    return a + b;
}

// 2. 多参数模板函数
template<typename T, typename U, size_t N>
void process_data(T data, U processor, const std::array<int, N>& config) {
    std::cout << "Processing data with " << N << " config items" << std::endl;
}

// 3. 模板函数的完全特化
template<>
int add_values<int>(int a, int b);

template<>
std::string add_values<std::string>(std::string a, std::string b);

// 4. 复杂的模板类 - 多个模板参数
template<typename T, typename U = int, size_t Size = 10>
class DataProcessor {
public:
    DataProcessor();
    ~DataProcessor();
    
    // 成员函数模板
    template<typename V>
    void process(V value);
    
    // 模板成员函数的特化声明
    template<>
    void process<double>(double value);
    
    void process_basic(T data, U index);
    
    // 静态成员函数模板
    template<typename K>
    static K convert(const T& input);
    
private:
    T data_[Size];
    U counter_;
};

// 5. 模板类的偏特化 - 指针类型
template<typename T, size_t Size>
class DataProcessor<T*, int, Size> {
public:
    DataProcessor();
    void process_pointer(T* ptr);
    void cleanup();
private:
    std::vector<T*> pointers_;
};

// 6. 模板类的完全特化
template<>
class DataProcessor<std::string, std::string, 5> {
public:
    DataProcessor();
    void process_strings();
    void add_string(const std::string& str);
private:
    std::vector<std::string> strings_;
};

// 7. 可变参数模板函数
template<typename... Args>
void log_values(Args... args) {
    ((std::cout << args << " "), ...);
    std::cout << std::endl;
}

// 8. 模板类继承
template<typename T>
class BaseTemplate {
public:
    BaseTemplate();
    virtual ~BaseTemplate();
    virtual void base_method(T value) = 0;
protected:
    T base_value_;
};

template<typename T, typename U>
class DerivedTemplate : public BaseTemplate<T> {
public:
    DerivedTemplate(T t_val, U u_val);
    void base_method(T value) override;
    void derived_method(U value);
    
    // 嵌套模板函数
    template<typename V>
    void nested_template_method(V v);
    
private:
    U derived_value_;
};

// 9. 函数对象模板
template<typename T>
struct Comparator {
    bool operator()(const T& a, const T& b) const {
        return a < b;
    }
};

// 10. 模板别名
template<typename T>
using ProcessorPtr = std::unique_ptr<DataProcessor<T>>;

template<typename T, typename U>
using ProcessorPair = std::pair<DataProcessor<T>, DataProcessor<U>>;

// 11. 约束模板（C++20风格，但用SFINAE实现）
template<typename T>
typename std::enable_if<std::is_arithmetic<T>::value, T>::type
constrained_add(T a, T b) {
    return a + b;
}

// 12. 递归模板
template<int N>
struct Factorial {
    static constexpr int value = N * Factorial<N-1>::value;
};

template<>
struct Factorial<0> {
    static constexpr int value = 1;
};

// 13. 模板模板参数
template<template<typename, typename> class Container, typename T>
class TemplateTemplateExample {
public:
    TemplateTemplateExample();
    void add_item(const T& item);
    void process_container();
private:
    Container<T, std::allocator<T>> container_;
};

// 14. 用于测试跨类调用的辅助类
class TemplateUser {
public:
    TemplateUser();
    
    // 调用各种模板函数
    void use_templates();
    
    // 调用模板类
    void use_template_classes();
    
    // 复杂的调用链
    void complex_call_chain();
    
private:
    DataProcessor<int> int_processor_;
    DataProcessor<std::string, std::string, 5> string_processor_;
};

// 15. 函数重载 - 用于测试函数解析
void overloaded_function(int x);
void overloaded_function(double x);
void overloaded_function(const std::string& x);

template<typename T>
void overloaded_function(const std::vector<T>& x);

// 16. 声明但不定义的函数 - 用于测试is_definition验证
void declared_only_template_func();

template<typename T>
void declared_only_template(T value);

// 17. 内联函数 - 有定义
inline int inline_template_func(int x) {
    return x * x;
}

template<typename T>
inline T inline_template(T value) {
    return value + value;
}

} // namespace Validation

#endif // VALIDATION_ADVANCED_TEMPLATES_H