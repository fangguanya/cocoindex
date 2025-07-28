#include "derived.h"
#include "utils.h"
#include "templates.h"
#include "complex.h"
#include "advanced_templates.h"
#include <iostream>
#include <memory>
#include <vector>
#include <array>

void process_base(Validation::Base* obj) {
    obj->commonMethod(); // Virtual call
    std::cout << "Type: " << obj->getTypeName() << std::endl;
}

int main() {
    std::cout << "=== 开始高级C++分析测试 ===" << std::endl;
    
    // --- Basic tests ---
    std::cout << "\n--- 基础测试 ---" << std::endl;
    Validation::print_message(100);
    Validation::Derived derived_obj(1, "MyDerived");
    process_base(&derived_obj);

    // --- Complex call tests ---
    std::cout << "\n--- 复杂调用测试 ---" << std::endl;
    ComplexClass complex_obj;
    complex_obj.defined_in_source();

    // --- Advanced Template Tests ---
    std::cout << "\n--- 高级模板测试 ---" << std::endl;
    
    // 1. Test primary class template
    Validation::AdvancedContainer<int, double> ac_int_double;
    ac_int_double.add(10, 3.14);
    ac_int_double.process(true); // Calls generic member template
    ac_int_double.process("a string"); // Calls specialized member template

    // 2. Test partially specialized class template
    int x = 5;
    Validation::AdvancedContainer<int*, float> ac_ptr_float;
    ac_ptr_float.configure(&x, 2.71f);

    // 3. Test cross-class template calls
    Validation::TemplateCaller caller;
    caller.do_work();

    // --- 新增的高级模板功能测试 ---
    std::cout << "\n--- 新增高级模板功能测试 ---" << std::endl;
    
    // 4. 测试模板函数特化
    auto int_result = Validation::add_values(10, 20);
    auto string_result = Validation::add_values<std::string>("Hello", "World");
    
    // 5. 测试复杂模板类
    Validation::DataProcessor<int, int, 10> processor;
    processor.process_basic(42, 1);
    processor.process(3.14);
    
    // 6. 测试模板类完全特化
    Validation::DataProcessor<std::string, std::string, 5> string_processor;
    string_processor.add_string("test1");
    string_processor.add_string("test2");
    string_processor.process_strings();
    
    // 7. 测试模板类指针特化
    double value = 99.9;
    Validation::DataProcessor<double*, int, 3> ptr_processor;
    ptr_processor.process_pointer(&value);
    ptr_processor.cleanup();
    
    // 8. 测试模板继承
    Validation::DerivedTemplate<int, std::string> derived_template(100, "derived");
    derived_template.derived_method("test");
    derived_template.nested_template_method(42.5);
    
    // 9. 测试可变参数模板
    Validation::log_values(1, 2.5, "variadic", 'x', true);
    
    // 10. 测试约束模板
    auto constrained_result = Validation::constrained_add(5.5, 3.2);
    
    // 11. 测试递归模板
    constexpr int factorial_5 = Validation::Factorial<5>::value;
    std::cout << "Factorial<5> = " << factorial_5 << std::endl;
    
    // 12. 测试模板模板参数
    Validation::TemplateTemplateExample<std::vector, int> container_example;
    container_example.add_item(1);
    container_example.add_item(2);
    container_example.add_item(3);
    container_example.process_container();
    
    // 13. 测试函数重载
    Validation::overloaded_function(42);
    Validation::overloaded_function(3.14);
    Validation::overloaded_function("overload_test");
    std::vector<int> vec{1, 2, 3, 4, 5};
    Validation::overloaded_function(vec);
    
    // 14. 测试函数对象
    Validation::Comparator<int> comp;
    bool comparison_result = comp(10, 20);
    std::cout << "Comparison result: " << comparison_result << std::endl;
    
    // 15. 测试内联模板函数
    auto inline_result = Validation::inline_template_func(7);
    auto inline_template_result = Validation::inline_template(42);
    
    // 16. 测试TemplateUser类 - 复杂的跨类调用
    std::cout << "\n--- TemplateUser 复杂调用测试 ---" << std::endl;
    Validation::TemplateUser template_user;
    template_user.use_templates();
    template_user.use_template_classes();
    template_user.complex_call_chain();
    
    // 17. 测试静态成员函数模板
    auto converted_value = Validation::DataProcessor<int>::convert<double>(123);
    
    // 18. 测试多参数模板函数
    std::array<int, 4> config{10, 20, 30, 40};
    Validation::process_data(42, "processor", config);
    
    std::cout << "\n=== 所有测试完成 ===" << std::endl;
    return 0;
}