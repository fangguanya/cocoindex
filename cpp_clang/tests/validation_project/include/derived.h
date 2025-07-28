#ifndef VALIDATION_DERIVED_H
#define VALIDATION_DERIVED_H

#include "base.h"
#include "another_base.h"
#include <string>

namespace Validation {

class Derived : public Base, public AnotherBase {
public:
    Derived(int id, std::string name);
    ~Derived();

    void commonMethod() override; // Override Base::commonMethod
    std::string getTypeName() const override; // Implement pure virtual function
    void anotherMethod() override; // Implement pure virtual function from AnotherBase

    void uniqueDerivedMethod();

private:
    std::string derived_name;
};

} // namespace Validation

#endif // VALIDATION_DERIVED_H