#include "advanced_templates.h"
#include "utils.h"
#include "base.h"
#include "derived.h"
#include <array>
#include <algorithm>

namespace Validation {

// 1. 模板函数的完全特化实现
template<>
int add_values<int>(int a, int b) {
    std::cout << "Specialized add_values for int: " << a << " + " << b << std::endl;
    // 调用其他函数来增加复杂度
    print_message(a + b);
    return a + b;
}

template<>
std::string add_values<std::string>(std::string a, std::string b) {
    std::cout << "Specialized add_values for string" << std::endl;
    print_message("Concatenating strings");
    return a + b;
}

// 2. DataProcessor 模板类的实现
template<typename T, typename U, size_t Size>
DataProcessor<T, U, Size>::DataProcessor() : counter_(U{}) {
    std::cout << "DataProcessor constructor with Size=" << Size << std::endl;
    // 调用全局函数
    global_util_func(static_cast<int>(Size));
}

template<typename T, typename U, size_t Size>
DataProcessor<T, U, Size>::~DataProcessor() {
    std::cout << "DataProcessor destructor" << std::endl;
}

template<typename T, typename U, size_t Size>
template<typename V>
void DataProcessor<T, U, Size>::process(V value) {
    std::cout << "Generic process method" << std::endl;
    // 调用模板函数
    auto result = add_values(value, value);
    print_message("Processing completed");
}

// 修复：成员函数模板特化需要在类外定义
template<>
template<>
void DataProcessor<int, int, 10>::process<double>(double value) {
    std::cout << "Specialized process for double: " << value << std::endl;
    // 复杂的函数调用链
    print_message(static_cast<int>(value));
    global_util_func(42);
}

template<typename T, typename U, size_t Size>
void DataProcessor<T, U, Size>::process_basic(T data, U index) {
    std::cout << "process_basic called" << std::endl;
    data_[0] = data;
    counter_ = index;
    // 调用其他成员
    process(data);
}

template<typename T, typename U, size_t Size>
template<typename K>
K DataProcessor<T, U, Size>::convert(const T& input) {
    std::cout << "Static convert method" << std::endl;
    return K{};
}

// 3. DataProcessor 指针特化的实现
template<typename T, size_t Size>
DataProcessor<T*, int, Size>::DataProcessor() {
    std::cout << "DataProcessor pointer specialization constructor" << std::endl;
    pointers_.reserve(Size);
}

template<typename T, size_t Size>
void DataProcessor<T*, int, Size>::process_pointer(T* ptr) {
    std::cout << "Processing pointer" << std::endl;
    if (ptr) {
        pointers_.push_back(ptr);
        // 调用模板函数
        add_values(*ptr, *ptr);
    }
    print_message("Pointer processed");
}

template<typename T, size_t Size>
void DataProcessor<T*, int, Size>::cleanup() {
    std::cout << "Cleaning up pointers" << std::endl;
    pointers_.clear();
    global_util_func(static_cast<int>(pointers_.size()));
}

// 4. DataProcessor 完全特化的实现
DataProcessor<std::string, std::string, 5>::DataProcessor() {
    std::cout << "DataProcessor string specialization constructor" << std::endl;
    strings_.reserve(5);
}

void DataProcessor<std::string, std::string, 5>::process_strings() {
    std::cout << "Processing strings in specialized class" << std::endl;
    for (const auto& str : strings_) {
        print_message(str);
        // 调用特化的模板函数
        add_values<std::string>(str, "_processed");
    }
}

void DataProcessor<std::string, std::string, 5>::add_string(const std::string& str) {
    std::cout << "Adding string: " << str << std::endl;
    strings_.push_back(str);
    print_message("String added to collection");
}

// 5. BaseTemplate 的实现
template<typename T>
BaseTemplate<T>::BaseTemplate() : base_value_(T{}) {
    std::cout << "BaseTemplate constructor" << std::endl;
}

template<typename T>
BaseTemplate<T>::~BaseTemplate() {
    std::cout << "BaseTemplate destructor" << std::endl;
}

// 6. DerivedTemplate 的实现
template<typename T, typename U>
DerivedTemplate<T, U>::DerivedTemplate(T t_val, U u_val) 
    : BaseTemplate<T>(), derived_value_(u_val) {
    this->base_value_ = t_val;
    std::cout << "DerivedTemplate constructor" << std::endl;
    // 调用父类的虚函数
    this->base_method(t_val);
}

template<typename T, typename U>
void DerivedTemplate<T, U>::base_method(T value) {
    std::cout << "DerivedTemplate::base_method implementation" << std::endl;
    this->base_value_ = value;
    // 调用全局函数
    print_message("Base method called");
}

template<typename T, typename U>
void DerivedTemplate<T, U>::derived_method(U value) {
    std::cout << "DerivedTemplate::derived_method" << std::endl;
    derived_value_ = value;
    // 调用模板函数
    auto result = add_values(value, value);
    global_util_func(123);
}

template<typename T, typename U>
template<typename V>
void DerivedTemplate<T, U>::nested_template_method(V v) {
    std::cout << "Nested template method" << std::endl;
    // 复杂的调用链
    std::array<int, 3> config{1, 2, 3};
    process_data(this->base_value_, derived_value_, config);
    add_values(v, v);
}

// 7. TemplateTemplateExample 的实现
template<template<typename, typename> class Container, typename T>
TemplateTemplateExample<Container, T>::TemplateTemplateExample() {
    std::cout << "TemplateTemplateExample constructor" << std::endl;
}

template<template<typename, typename> class Container, typename T>
void TemplateTemplateExample<Container, T>::add_item(const T& item) {
    std::cout << "Adding item to container" << std::endl;
    container_.push_back(item);
    print_message("Item added");
}

template<template<typename, typename> class Container, typename T>
void TemplateTemplateExample<Container, T>::process_container() {
    std::cout << "Processing container with " << container_.size() << " items" << std::endl;
    for (const auto& item : container_) {
        add_values(item, item);
    }
}

// 8. TemplateUser 的实现
TemplateUser::TemplateUser() {
    std::cout << "TemplateUser constructor" << std::endl;
    // 在构造函数中就开始调用
    global_util_func(100);
}

void TemplateUser::use_templates() {
    std::cout << "\n=== TemplateUser::use_templates ===" << std::endl;
    
    // 调用各种模板函数
    auto int_result = add_values(10, 20);
    auto string_result = add_values<std::string>("Hello", "World");
    
    // 调用可变参数模板
    log_values(1, 2.5, "test", 'c');
    
    // 调用约束模板
    auto constrained_result = constrained_add(5.5, 3.2);
    
    // 调用内联模板
    auto inline_result = inline_template(42);
    
    print_message("Templates used successfully");
}

void TemplateUser::use_template_classes() {
    std::cout << "\n=== TemplateUser::use_template_classes ===" << std::endl;
    
    // 使用不同的模板类实例
    DataProcessor<double, int, 8> double_processor;
    double_processor.process_basic(3.14, 1);
    double_processor.process(2.71);
    
    // 使用指针特化
    int value = 42;
    DataProcessor<int*, int, 5> pointer_processor;
    pointer_processor.process_pointer(&value);
    pointer_processor.cleanup();
    
    // 使用完全特化
    string_processor_.add_string("test1");
    string_processor_.add_string("test2");
    string_processor_.process_strings();
    
    // 使用继承的模板类
    DerivedTemplate<int, std::string> derived(100, "derived");
    derived.derived_method("method_call");
    derived.nested_template_method(99.9);
    
    print_message("Template classes used successfully");
}

void TemplateUser::complex_call_chain() {
    std::cout << "\n=== TemplateUser::complex_call_chain ===" << std::endl;
    
    // 复杂的调用链，涉及多个类和函数
    
    // 1. 创建Derived对象并调用虚函数
    auto derived_obj = std::make_unique<Derived>(1, "complex_test");
    
    // 2. 使用模板模板参数
    TemplateTemplateExample<std::vector, int> container_example;
    container_example.add_item(1);
    container_example.add_item(2);
    container_example.process_container();
    
    // 3. 调用重载函数
    overloaded_function(42);
    overloaded_function(3.14);
    overloaded_function("overload_test");
    std::vector<int> vec{1, 2, 3};
    overloaded_function(vec);
    
    // 4. 使用函数对象
    Comparator<int> comp;
    bool result = comp(5, 10);
    
    // 5. 使用递归模板
    constexpr int fact5 = Factorial<5>::value;
    print_message(fact5);
    
    // 6. 静态成员函数调用
    auto converted = DataProcessor<int>::convert<double>(42);
    
    // 7. 调用calculate函数增加跨文件调用
    calculate(fact5, 10);
    
    print_message("Complex call chain completed");
}

// 9. 重载函数的实现
void overloaded_function(int x) {
    std::cout << "overloaded_function(int): " << x << std::endl;
    print_message(x);
}

void overloaded_function(double x) {
    std::cout << "overloaded_function(double): " << x << std::endl;
    global_util_func(static_cast<int>(x));
}

void overloaded_function(const std::string& x) {
    std::cout << "overloaded_function(string): " << x << std::endl;
    print_message(x);
}

template<typename T>
void overloaded_function(const std::vector<T>& x) {
    std::cout << "overloaded_function(vector) with " << x.size() << " elements" << std::endl;
    for (const auto& item : x) {
        add_values(item, item);
    }
    print_message("Vector processed");
}

// 10. 显式实例化一些模板，确保它们被编译
template class DataProcessor<int, int, 10>;
template class DataProcessor<double, int, 8>;
template class DataProcessor<float*, int, 5>;

template class BaseTemplate<int>;
template class DerivedTemplate<int, std::string>;
template class DerivedTemplate<double, int>;

template class TemplateTemplateExample<std::vector, int>;
template class TemplateTemplateExample<std::vector, std::string>;

// 显式实例化一些模板函数
// template int add_values<int>(int, int);  // 注释掉，因为已经有显式特化
template double add_values<double>(double, double);

template void process_data<int, std::string, 3>(int, std::string, const std::array<int, 3>&);

template void log_values<int, double, std::string>(int, double, std::string);

template void overloaded_function<int>(const std::vector<int>&);
template void overloaded_function<double>(const std::vector<double>&);

} // namespace Validation