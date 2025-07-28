#include "templates.h"
#include "utils.h" // For cross-class calls

namespace Validation {

// --- Existing template implementations ---

template<>
void print_value<std::string>(std::string value) {
    std::cout << "Specialized template print for string: " << value << std::endl;
}

template<>
int get_default<int>() {
    return 42;
}

// --- Advanced Template Implementations ---

// 1. Implementation for the primary class template
template<typename T, typename U>
void AdvancedContainer<T, U>::add(T t, U u) {
    std::cout << "AdvancedContainer::add called." << std::endl;
    print_value(t);
    print_message(sizeof(u));
}

// 2. Implementation for the member function template
template<typename T, typename U>
template<typename V>
void AdvancedContainer<T, U>::process(V v) {
    std::cout << "AdvancedContainer::process with V type" << std::endl;
    print_value(v);
}

// 3. Explicit specialization for the member function template
template<>
template<>
void AdvancedContainer<int, double>::process<std::string>(std::string v) {
    std::cout << "AdvancedContainer<int, double>::process<std::string> EXPLICITLY SPECIALIZED" << std::endl;
    print_message(v);
}

// 4. Implementation for the partially specialized class
template<typename T, typename U>
void AdvancedContainer<T*, U>::configure(T* t, U u) {
    std::cout << "Partially specialized AdvancedContainer<T*, U>::configure called." << std::endl;
    if (t) {
        print_value(*t);
    }
    print_value(u);
}

// 5. Implementation for the cross-class caller
void TemplateCaller::do_work() {
    std::cout << "\n--- TemplateCaller::do_work ---" << std::endl;
    AdvancedContainer<double, int> ac;
    ac.add(3.14, 10);
    ac.process(true); // Calls generic member template

    AdvancedContainer<int, double> ac_special;
    ac_special.process("hello"); // Calls specialized member template
}


// --- Explicit Instantiations to ensure code generation ---
template class AdvancedContainer<double, int>;
template class AdvancedContainer<int*, float>;

} // namespace Validation