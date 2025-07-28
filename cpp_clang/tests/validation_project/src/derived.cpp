#include "derived.h"
#include "utils.h" // For calling overloaded functions
#include <iostream>

namespace Validation {

Derived::Derived(int id, std::string name) : Base(id), derived_name(name) {
    std::cout << "Derived constructor called." << std::endl;
}

Derived::~Derived() {
    std::cout << "Derived destructor called." << std::endl;
}

void Derived::commonMethod() {
    std::cout << "Derived::commonMethod override called." << std::endl;
    Base::commonMethod(); // Call to base class method
}

std::string Derived::getTypeName() const {
    return "Derived";
}

void Derived::anotherMethod() {
    std::cout << "Derived::anotherMethod implemented." << std::endl;
}

void Derived::uniqueDerivedMethod() {
    std::cout << "Derived::uniqueDerivedMethod called." << std::endl;
    // Call overloaded functions from utils
    print_message(42);
    print_message("hello from derived");
}

} // namespace Validation