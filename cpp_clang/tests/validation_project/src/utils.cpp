#include "utils.h"
#include <iostream>

void global_util_func(int value) {
    std::cout << "Global util func called with: " << value << std::endl;
}

namespace Validation {

void print_message(int number) {
    std::cout << "Printing number: " << number << std::endl;
}

void print_message(const std::string& text) {
    std::cout << "Printing text: " << text << std::endl;
}

int calculate(int a, int b) {
    // Call another function in the same file
    print_message("Calculating...");
    return a + b;
}

} // namespace Validation