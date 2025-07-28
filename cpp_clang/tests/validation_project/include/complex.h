#ifndef COMPLEX_H
#define COMPLEX_H

// Function declared but not defined in any cpp file
void declared_only_func();

// Function defined inline in the header
inline int inline_func(int a) {
    return a * a;
}

class ComplexClass {
public:
    // Constructor declaration
    ComplexClass();

    // Member function defined in the header
    void defined_in_header() {
        // Do nothing
    }

    // Member function declared here, defined in cpp
    void defined_in_source();

    // Member function only declared, not defined anywhere
    void declared_only_member();
};

#endif // COMPLEX_H