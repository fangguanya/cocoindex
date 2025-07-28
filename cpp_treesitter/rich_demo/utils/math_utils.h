#ifndef MATH_UTILS_H
#define MATH_UTILS_H

#include <vector>

// 数学工具类
class MathUtils {
public:
    // 静态方法
    static int add(int a, int b);
    static double add(double a, double b);
    static int multiply(int a, int b);
    static double multiply(double a, double b);
    static double divide(double a, double b);
    
    // 高级数学函数
    static double power(double base, int exponent);
    static double factorial(int n);
    static bool isPrime(int number);
    
    // 向量操作
    static int sum(const std::vector<int>& numbers);
    static double average(const std::vector<int>& numbers);
    static int findMax(const std::vector<int>& numbers);
    static int findMin(const std::vector<int>& numbers);
    
    // 私有辅助方法
private:
    static bool isValidNumber(double number);
    static double recursiveFactorial(int n);
};

// 全局数学常量命名空间
namespace MathConstants {
    extern const double PI;
    extern const double E;
    extern const double GOLDEN_RATIO;
}

// 模板函数
template<typename T>
T clamp(T value, T min, T max) {
    if (value < min) return min;
    if (value > max) return max;
    return value;
}

template<typename T>
void swap(T& a, T& b) {
    T temp = a;
    a = b;
    b = temp;
}

#endif // MATH_UTILS_H 