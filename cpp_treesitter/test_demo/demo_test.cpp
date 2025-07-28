#include <iostream>
#include <string>

// 基类
class Base {
public:
    virtual void display() = 0;
    virtual ~Base() = default;
};

// 派生类
class Derived : public Base {
public:
    void display() override { std::cout << "Derived class" << std::endl; }
    
    // 新增：跨类型调用方法 - 调用另一个类的方法
    void callAnother() {
        Another another;
        another.show(); // 跨类型调用：Derived -> Another
    }
};

// 另一个类，用于多重继承测试
class Another {
public:
    virtual void show() { std::cout << "Another class" << std::endl; }
    virtual ~Another() = default;
    
    // 新增：跨类型调用方法 - 调用基类方法
    void callBase() {
        Base *base = new Derived();
        base->display(); // 跨类型调用：Another -> Base
    }
};

// 多重继承类
class MultipleInherited : public Base, public Another {
public:
    void display() override { std::cout << "Multiple inherited class" << std::endl; }
    void show() override { std::cout << "Multiple inherited show" << std::endl; }
    
    // 新增：调用模板函数
    void callTemplateFunction() {
        auto result = maxValue(100, 200); // 跨类型调用：MultipleInherited -> 模板函数
        std::cout << "Template result: " << result << std::endl;
    }
};

// 新增：跨类型调用测试类
class CrossCallTester {
public:
    // 调用不同类的方法
    void testAllClasses() {
        Base* base = new Derived();
        base->display(); // 多态调用
        delete base;
        
        Derived derived;
        derived.callAnother(); // 调用Derived的跨类型方法
        
        Another another;
        another.callBase(); // 调用Another的跨类型方法
        
        MultipleInherited multi;
        multi.callTemplateFunction(); // 调用MultipleInherited的模板函数调用方法
    }
    
    // 测试重载函数调用
    void testOverloadedFunctions() {
        processData(999); // 调用int版本
        processData("CrossCallTester"); // 调用string版本
    }
    
    // 测试递归和相互调用
    void testRecursiveAndMutualCalls() {
        int result = factorial(3);
        std::cout << "Factorial from CrossCallTester: " << result << std::endl;
        functionA(); // 调用相互调用的函数
    }
};

// 命名空间
namespace TestNamespace {
    class NamespaceClass {
    public:
        void namespaceMethod() { std::cout << "Namespace method" << std::endl; }
        
        // 跨类型调用方法 - 调用 Another 类的方法
        void callAnotherClass() {
            Another another;
            another.show(); // 跨类型调用：NamespaceClass -> Another
        }
        
        // 跨类型调用方法 - 调用 Derived 类的方法
        void callDerivedClass() {
            Derived derived;
            derived.display(); // 跨类型调用：NamespaceClass -> Derived
        }
        
        // 新增：调用新的跨类型测试类
        void callCrossCallTester() {
            CrossCallTester tester;
            tester.testAllClasses(); // 跨类型调用：NamespaceClass -> CrossCallTester
        }
    };
    
    void namespaceFunction() {
        std::cout << "Namespace function" << std::endl;
    }
    
    // 新增：命名空间中的跨类型调用函数
    void namespaceCrossCall() {
        CrossCallTester tester;
        tester.testOverloadedFunctions(); // 跨类型调用：命名空间函数 -> CrossCallTester
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
    nc.callAnotherClass();  // 跨类型调用测试
    nc.callDerivedClass();  // 跨类型调用测试
    nc.callCrossCallTester(); // 新增：跨类型调用测试
    TestNamespace::namespaceFunction();
    TestNamespace::namespaceCrossCall(); // 新增：命名空间跨类型调用测试
    
    // 测试模板
    std::cout << "Max: " << maxValue(10, 20) << std::endl;
    
    // 测试重载 - 这里是问题所在
    processData(42);
    processData("Hello");
    
    // 测试递归
    std::cout << "Factorial: " << factorial(5) << std::endl;
    
    // 测试函数调用
    functionA();
    
    // 新增：测试跨类型调用测试类
    CrossCallTester tester;
    tester.testAllClasses();
    tester.testOverloadedFunctions();
    tester.testRecursiveAndMutualCalls();
    
    return 0;
} 