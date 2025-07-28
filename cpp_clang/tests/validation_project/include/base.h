#ifndef VALIDATION_BASE_H
#define VALIDATION_BASE_H

#include <string>

namespace Validation {

class Base {
public:
    Base(int id);
    virtual ~Base();

    virtual void commonMethod();
    virtual std::string getTypeName() const = 0; // Pure virtual function

protected:
    int base_id;
};

} // namespace Validation

#endif // VALIDATION_BASE_H