#include "geometry.h"
#include <cmath>

// Shape类实现
Shape::Shape(const std::string& shapeName) : name(shapeName) {
    std::cout << "Shape constructor: " << name << std::endl;
}

std::string Shape::getName() const {
    return name;
}

void Shape::setName(const std::string& newName) {
    name = newName;
}

// Circle类实现
Circle::Circle(double r) : Shape("Circle"), radius(r) {
    if (!GeometryUtils::isValidDimension(r)) {
        radius = 1.0;
    }
}

Circle::Circle(const std::string& name, double r) : Shape(name), radius(r) {
    if (!GeometryUtils::isValidDimension(r)) {
        radius = 1.0;
    }
}

double Circle::calculateArea() const {
    return M_PI * radius * radius;
}

void Circle::display() const {
    std::cout << "Circle: " << getName() << " (radius: " << radius << ")" << std::endl;
}

double Circle::getRadius() const {
    return radius;
}

void Circle::setRadius(double r) {
    if (GeometryUtils::isValidDimension(r)) {
        radius = r;
    }
}

double Circle::calculateCircumference() const {
    return 2 * M_PI * radius;
}

// Rectangle类实现
Rectangle::Rectangle(double w, double h) : Shape("Rectangle"), width(w), height(h) {
    if (!GeometryUtils::isValidDimension(w) || !GeometryUtils::isValidDimension(h)) {
        width = height = 1.0;
    }
}

Rectangle::Rectangle(const std::string& name, double w, double h) : Shape(name), width(w), height(h) {
    if (!GeometryUtils::isValidDimension(w) || !GeometryUtils::isValidDimension(h)) {
        width = height = 1.0;
    }
}

double Rectangle::calculateArea() const {
    return width * height;
}

void Rectangle::display() const {
    std::cout << "Rectangle: " << getName() << " (" << width << "x" << height << ")" << std::endl;
}

double Rectangle::getWidth() const {
    return width;
}

double Rectangle::getHeight() const {
    return height;
}

void Rectangle::setDimensions(double w, double h) {
    if (GeometryUtils::isValidDimension(w) && GeometryUtils::isValidDimension(h)) {
        width = w;
        height = h;
    }
}

double Rectangle::calculatePerimeter() const {
    return 2 * (width + height);
}

// GeometryUtils命名空间实现
namespace GeometryUtils {
    double calculateDistance(double x1, double y1, double x2, double y2) {
        double dx = x2 - x1;
        double dy = y2 - y1;
        return std::sqrt(dx * dx + dy * dy);
    }
    
    bool isValidDimension(double value) {
        return value > 0.0;
    }
    
    void printShapeInfo(const Shape& shape) {
        std::cout << "Shape information:" << std::endl;
        shape.display();
        std::cout << "Area: " << shape.calculateArea() << std::endl;
    }
} 