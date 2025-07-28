#ifndef VALIDATION_UTILS_H
#define VALIDATION_UTILS_H

#include <string>

// A global function
void global_util_func(int value);

namespace Validation {

// Overloaded functions
void print_message(int number);
void print_message(const std::string& text);

// A new function inside the namespace
int calculate(int a, int b);

} // namespace Validation

#endif // VALIDATION_UTILS_H