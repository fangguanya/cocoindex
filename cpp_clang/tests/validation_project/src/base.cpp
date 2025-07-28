#include "base.h"
#include <iostream>

namespace Validation {

Base::Base(int id) : base_id(id) {
    std::cout << "Base constructor called." << std::endl;
}

Base::~Base() {
    std::cout << "Base destructor called." << std::endl;
}

void Base::commonMethod() {
    std::cout << "Base::commonMethod called." << std::endl;
}

} // namespace Validation