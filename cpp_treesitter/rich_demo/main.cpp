#include <iostream>
#include <vector>
#include <memory>
#include "geometry.h"
#include "utils/math_utils.h"
#include "data/person.h"
#include "advanced/template_container.h"
#include "advanced/multiple_inheritance.h"
#include "interactions.h" // 跨类调用

// 全局函数
void globalFunction() {
    std::cout << "Global function called" << std::endl;
}

// 模板函数
template<typename T>
T maxValue(T a, T b) {
    return (a > b) ? a : b;
}

// 函数模板特化
template<>
const char* maxValue<const char*>(const char* a, const char* b) {
    std::cout << "Using specialized maxValue for const char*" << std::endl;
    return strcmp(a, b) > 0 ? a : b;
}

// 函数重载
void processData(int value) {
    std::cout << "Processing int: " << value << std::endl;
}

void processData(const std::string& value) {
    std::cout << "Processing string: " << value << std::endl;
}

void processData(const std::vector<int>& values) {
    std::cout << "Processing vector of size: " << values.size() << std::endl;
}

int main() {
    // 调用全局函数
    globalFunction();
    
    // 调用数学工具函数
    int sum = MathUtils::add(10, 20);
    double product = MathUtils::multiply(3.14, 2.0);
    std::cout << "Sum: " << sum << ", Product: " << product << std::endl;
    
    // 创建几何图形对象
    Circle circle(5.0);
    Rectangle rect(4.0, 6.0);
    
    std::cout << "Circle area: " << circle.calculateArea() << std::endl;
    std::cout << "Rectangle area: " << rect.calculateArea() << std::endl;
    
    // 多态调用
    std::vector<std::unique_ptr<Shape>> shapes;
    shapes.push_back(std::make_unique<Circle>(3.0));
    shapes.push_back(std::make_unique<Rectangle>(2.0, 8.0));
    
    for (const auto& shape : shapes) {
        shape->display();
        std::cout << "Area: " << shape->calculateArea() << std::endl;
    }
    
    // 创建Person对象并调用方法
    Person person("Alice", 30);
    person.displayInfo();
    person.setAge(31);
    person.displayInfo();
    
    // 使用模板函数
    int maxInt = maxValue(100, 200);
    double maxDouble = maxValue(3.14, 2.71);
    std::cout << "Max int: " << maxInt << ", Max double: " << maxDouble << std::endl;

    // 函数重载测试
    processData(42);
    processData("Hello World");
    processData(std::vector<int>{1, 2, 3, 4, 5});
    
    // 模板类演示
    std::cout << "\n=== Template Classes Demo ===" << std::endl;
    Container<int> intContainer;
    intContainer.add(1);
    intContainer.add(2);
    intContainer.add(3);
    intContainer.print();
    
    Pair<std::string, int> namePair("Age", 25);
    std::cout << "Pair: " << namePair.getKey() << " = " << namePair.getValue() << std::endl;
    
    Stack<std::string> stringStack;
    stringStack.push("First");
    stringStack.push("Second");
    stringStack.push("Third");
    stringStack.print();
    
    Container<std::string> stringContainer;
    stringContainer.add("hello");
    stringContainer.add("world");
    stringContainer.printUpperCase();
    
    // 多重继承演示
    std::cout << "\n=== Multiple Inheritance Demo ===" << std::endl;
    Document doc("DOC001", "MyDocument", "This is a sample document");
    doc.print();
    std::cout << "Serialized: " << doc.serialize() << std::endl;
    
    MultimediaFile videoFile("VID001", "SampleVideo", "Video content data", "video/mp4");
    videoFile.setFileSize(1024000);
    videoFile.addMetadata("Resolution: 1920x1080");
    videoFile.addMetadata("Duration: 120 seconds");
    videoFile.print();
    videoFile.save("sample_video.json");
    
    // 钻石继承演示
    std::cout << "\n=== Diamond Inheritance Demo ===" << std::endl;
    Diamond diamond(100, 200, 300, 400);
    diamond.showAll();
    
    // New: Cross-class call demo
    Interactions::Manager manager;
    manager.manage();

    // New: Partial specialization demo
    Pair<double, int> partialPair(1.23, 42);

    return 0;
} 