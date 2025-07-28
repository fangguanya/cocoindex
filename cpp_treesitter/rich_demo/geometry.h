#ifndef GEOMETRY_H
#define GEOMETRY_H

#include <iostream>
#include <string>

// 抽象基类
class Shape {
protected:
    std::string name;
    
public:
    Shape(const std::string& shapeName);
    virtual ~Shape() = default;
    
    // 纯虚函数
    virtual double calculateArea() const = 0;
    virtual void display() const = 0;
    
    // 虚函数
    virtual std::string getName() const;
    virtual void setName(const std::string& newName);
};

// 圆形类
class Circle : public Shape {
private:
    double radius;
    
public:
    Circle(double r);
    Circle(const std::string& name, double r);
    
    // 重写虚函数
    double calculateArea() const override;
    void display() const override;
    
    // 特有方法
    double getRadius() const;
    void setRadius(double r);
    double calculateCircumference() const;
};

// 矩形类
class Rectangle : public Shape {
private:
    double width;
    double height;
    
public:
    Rectangle(double w, double h);
    Rectangle(const std::string& name, double w, double h);
    
    // 重写虚函数
    double calculateArea() const override;
    void display() const override;
    
    // 特有方法
    double getWidth() const;
    double getHeight() const;
    void setDimensions(double w, double h);
    double calculatePerimeter() const;
};

// 命名空间中的工具函数
namespace GeometryUtils {
    double calculateDistance(double x1, double y1, double x2, double y2);
    bool isValidDimension(double value);
    void printShapeInfo(const Shape& shape);
}

#endif // GEOMETRY_H 