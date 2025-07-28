#include "math_utils.h"
#include <iostream>
#include <stdexcept>
#include <cmath>
#include <algorithm>
#include <limits>

// MathUtils类实现
int MathUtils::add(int a, int b) {
    std::cout << "Adding integers: " << a << " + " << b << std::endl;
    return a + b;
}

double MathUtils::add(double a, double b) {
    std::cout << "Adding doubles: " << a << " + " << b << std::endl;
    if (!isValidNumber(a) || !isValidNumber(b)) {
        throw std::invalid_argument("Invalid number provided");
    }
    return a + b;
}

int MathUtils::multiply(int a, int b) {
    std::cout << "Multiplying integers: " << a << " * " << b << std::endl;
    return a * b;
}

double MathUtils::multiply(double a, double b) {
    std::cout << "Multiplying doubles: " << a << " * " << b << std::endl;
    if (!isValidNumber(a) || !isValidNumber(b)) {
        throw std::invalid_argument("Invalid number provided");
    }
    return a * b;
}

double MathUtils::divide(double a, double b) {
    if (!isValidNumber(a) || !isValidNumber(b)) {
        throw std::invalid_argument("Invalid number provided");
    }
    if (std::abs(b) < 1e-10) {
        throw std::invalid_argument("Division by zero");
    }
    return a / b;
}

double MathUtils::power(double base, int exponent) {
    if (!isValidNumber(base)) {
        throw std::invalid_argument("Invalid base provided");
    }
    
    if (exponent == 0) return 1.0;
    if (exponent == 1) return base;
    
    double result = 1.0;
    int absExp = std::abs(exponent);
    
    for (int i = 0; i < absExp; ++i) {
        result = multiply(result, base);
    }
    
    return (exponent < 0) ? divide(1.0, result) : result;
}

double MathUtils::factorial(int n) {
    if (n < 0) {
        throw std::invalid_argument("Factorial is not defined for negative numbers");
    }
    if (n > 20) {
        throw std::invalid_argument("Factorial too large to compute");
    }
    
    return recursiveFactorial(n);
}

bool MathUtils::isPrime(int number) {
    if (number < 2) return false;
    if (number == 2) return true;
    if (number % 2 == 0) return false;
    
    for (int i = 3; i * i <= number; i += 2) {
        if (number % i == 0) return false;
    }
    return true;
}

int MathUtils::sum(const std::vector<int>& numbers) {
    int total = 0;
    for (int num : numbers) {
        total = add(total, num);
    }
    return total;
}

double MathUtils::average(const std::vector<int>& numbers) {
    if (numbers.empty()) {
        throw std::invalid_argument("Cannot calculate average of empty vector");
    }
    int total = sum(numbers);
    return divide(static_cast<double>(total), static_cast<double>(numbers.size()));
}

int MathUtils::findMax(const std::vector<int>& numbers) {
    if (numbers.empty()) {
        throw std::invalid_argument("Cannot find max of empty vector");
    }
    return *std::max_element(numbers.begin(), numbers.end());
}

int MathUtils::findMin(const std::vector<int>& numbers) {
    if (numbers.empty()) {
        throw std::invalid_argument("Cannot find min of empty vector");
    }
    return *std::min_element(numbers.begin(), numbers.end());
}

// 私有方法
bool MathUtils::isValidNumber(double number) {
    return !std::isnan(number) && !std::isinf(number);
}

double MathUtils::recursiveFactorial(int n) {
    if (n <= 1) return 1.0;
    return multiply(static_cast<double>(n), recursiveFactorial(n - 1));
}

// 数学常量命名空间实现
namespace MathConstants {
    const double PI = 3.14159265358979323846;
    const double E = 2.71828182845904523536;
    const double GOLDEN_RATIO = 1.61803398874989484820;
} 