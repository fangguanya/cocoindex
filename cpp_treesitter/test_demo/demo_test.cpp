#include <iostream>
#include <string>

// 基类
class Base {
public:
    virtual void display() { std::cout << "Base class" << std::endl; }
    virtual ~Base() = default;
};

// 派生类
class Derived : public Base {
public:
    void display() override { std::cout << "Derived class" << std::endl; }
};

// 另一个类，用于多重继承测试
class Another {
public:
    virtual void show() { std::cout << "Another class" << std::endl; }
    virtual ~Another() = default;
};

// 多重继承类
class MultipleInherited : public Base, public Another {
public:
    void display() override { std::cout << "Multiple inherited class" << std::endl; }
    void show() override { std::cout << "Multiple inherited show" << std::endl; }
};

// 命名空间
namespace TestNamespace {
    class NamespaceClass {
    public:
        void namespaceMethod() { std::cout << "Namespace method" << std::endl; }
    };
    
    void namespaceFunction() {
        std::cout << "Namespace function" << std::endl;
    }
}

// 模板函数
template<typename T>
T maxValue(T a, T b) {
    return (a > b) ? a : b;
}

// 函数重载
void processData(int x) {
    std::cout << "Int version: " << x << std::endl;
}

void processData(const std::string& s) {
    std::cout << "String version: " << s << std::endl;
}

// 递归函数
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

// 相互调用函数
void functionA();
void functionB();

void functionA() {
    std::cout << "Function A" << std::endl;
    functionB();
}

void functionB() {
    std::cout << "Function B" << std::endl;
}

int main() {
    // 测试基本类和继承
    Base* base = new Derived();
    base->display();
    delete base;
    
    // 测试多重继承
    MultipleInherited multi;
    multi.display();
    multi.show();
    
    // 测试命名空间
    TestNamespace::NamespaceClass nc;
    nc.namespaceMethod();
    TestNamespace::namespaceFunction();
    
    // 测试模板
    std::cout << "Max: " << maxValue(10, 20) << std::endl;
    
    // 测试重载
    processData(42);
    processData("Hello");
    
    // 测试递归
    std::cout << "Factorial: " << factorial(5) << std::endl;
    
    // 测试函数调用
    functionA();
    
    return 0;
} 