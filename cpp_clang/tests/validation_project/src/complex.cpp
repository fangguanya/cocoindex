#include "complex.h"
#include "utils.h" // For calling another function

// Definition for the constructor
ComplexClass::ComplexClass() {
    // Call a global function from another file
    global_util_func(10);
}

// Definition for a member function
void ComplexClass::defined_in_source() {
    // Call a namespaced function
    Validation::calculate(5, 3);
}

// Note: declared_only_func() is not defined.
// Note: ComplexClass::declared_only_member() is not defined.