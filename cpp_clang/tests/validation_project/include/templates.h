#ifndef VALIDATION_TEMPLATES_H
#define VALIDATION_TEMPLATES_H

#include <iostream>
#include <string>
#include <vector>

namespace Validation {

// --- Existing templates for basic validation ---
template<typename T>
void print_value(T value) {
    std::cout << "Template print: " << value << std::endl;
}

template<typename T>
T get_default() {
    return T();
}

template<>
void print_value<std::string>(std::string value);

template<>
int get_default<int>();

// --- Advanced Template Scenarios ---

// 1. Class template with multiple parameters
template<typename T, typename U>
class AdvancedContainer {
public:
    void add(T t, U u);

    // 2. Member function template
    template<typename V>
    void process(V v);
};

// 3. Class template partial specialization (for pointer types)
template<typename T, typename U>
class AdvancedContainer<T*, U> {
public:
    void configure(T* t, U u);
};

// 4. A new class to test cross-class calls
class TemplateCaller {
public:
    void do_work();
};


} // namespace Validation

#endif // VALIDATION_TEMPLATES_H