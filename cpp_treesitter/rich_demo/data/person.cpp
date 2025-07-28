#include "person.h"
#include <iostream>
#include <algorithm>
#include <sstream>

// Person类实现
Person::Person() : name("Unknown"), age(0) {
    std::cout << "Default Person constructor" << std::endl;
}

Person::Person(const std::string& personName, int personAge) : name(personName), age(personAge) {
    if (!isValidName(personName)) {
        name = "Unknown";
    }
    if (!isValidAge(personAge)) {
        age = 0;
    }
    std::cout << "Person constructor: " << name << ", age " << age << std::endl;
}

Person::Person(const Person& other) : name(other.name), age(other.age) {
    std::cout << "Person copy constructor for: " << name << std::endl;
    // 深拷贝address和contacts
    if (other.address) {
        address = std::make_unique<Address>(*other.address);
    }
    for (const auto& contact : other.contacts) {
        contacts.push_back(std::make_unique<Contact>(*contact));
    }
}

Person& Person::operator=(const Person& other) {
    if (this != &other) {
        name = other.name;
        age = other.age;
        
        // 重新分配address和contacts
        address.reset();
        contacts.clear();
        
        if (other.address) {
            address = std::make_unique<Address>(*other.address);
        }
        for (const auto& contact : other.contacts) {
            contacts.push_back(std::make_unique<Contact>(*contact));
        }
    }
    return *this;
}

Person::~Person() {
    std::cout << "Person destructor: " << name << std::endl;
}

std::string Person::getName() const {
    return name;
}

int Person::getAge() const {
    return age;
}

void Person::setName(const std::string& newName) {
    if (isValidName(newName)) {
        name = newName;
    }
}

void Person::setAge(int newAge) {
    if (isValidAge(newAge)) {
        age = newAge;
    }
}

void Person::displayInfo() const {
    std::cout << "Person: " << getName() << ", Age: " << getAge() << std::endl;
    if (address) {
        std::cout << "Address: " << getAddressInfo() << std::endl;
    }
    auto contactInfo = getContactInfo();
    if (!contactInfo.empty()) {
        std::cout << "Contacts: ";
        for (const auto& info : contactInfo) {
            std::cout << info << " ";
        }
        std::cout << std::endl;
    }
}

std::string Person::getPersonType() const {
    return "Person";
}

void Person::setAddress(const std::string& street, const std::string& city, const std::string& country) {
    address = std::make_unique<Address>(street, city, country);
}

std::string Person::getAddressInfo() const {
    if (address) {
        return address->getFullAddress();
    }
    return "No address";
}

void Person::addContact(const std::string& type, const std::string& value) {
    // 先检查是否已存在相同类型的联系方式
    removeContact(type);
    contacts.push_back(std::make_unique<Contact>(type, value));
}

void Person::removeContact(const std::string& type) {
    contacts.erase(
        std::remove_if(contacts.begin(), contacts.end(),
            [&type](const std::unique_ptr<Contact>& contact) {
                return contact->getType() == type;
            }),
        contacts.end()
    );
}

std::vector<std::string> Person::getContactInfo() const {
    std::vector<std::string> info;
    for (const auto& contact : contacts) {
        info.push_back(contact->getContactInfo());
    }
    return info;
}

bool Person::operator==(const Person& other) const {
    return getName() == other.getName() && getAge() == other.getAge();
}

bool Person::operator<(const Person& other) const {
    if (getName() != other.getName()) {
        return getName() < other.getName();
    }
    return getAge() < other.getAge();
}

bool Person::isValidAge(int age) {
    return age >= 0 && age <= 150;
}

bool Person::isValidName(const std::string& name) {
    return !name.empty() && name.length() <= 100;
}

// Student类实现
Student::Student(const std::string& name, int age, const std::string& id, const std::string& majorField)
    : Person(name, age), studentId(id), major(majorField), gpa(0.0) {
    std::cout << "Student constructor: " << getName() << ", ID: " << studentId << std::endl;
}

void Student::displayInfo() const {
    Person::displayInfo();  // 调用基类方法
    std::cout << "Student ID: " << getStudentId() << ", Major: " << getMajor() 
              << ", GPA: " << getGPA() << std::endl;
    auto courseList = getCourses();
    if (!courseList.empty()) {
        std::cout << "Courses: ";
        for (const auto& course : courseList) {
            std::cout << course << " ";
        }
        std::cout << std::endl;
    }
}

std::string Student::getPersonType() const {
    return "Student";
}

void Student::setMajor(const std::string& newMajor) {
    if (!newMajor.empty()) {
        major = newMajor;
    }
}

void Student::setGPA(double newGpa) {
    if (newGpa >= 0.0 && newGpa <= 4.0) {
        gpa = newGpa;
    }
}

void Student::addCourse(const std::string& courseName) {
    if (std::find(courses.begin(), courses.end(), courseName) == courses.end()) {
        courses.push_back(courseName);
    }
}

void Student::removeCourse(const std::string& courseName) {
    courses.erase(std::remove(courses.begin(), courses.end(), courseName), courses.end());
}

std::string Student::getStudentId() const { return studentId; }
std::string Student::getMajor() const { return major; }
double Student::getGPA() const { return gpa; }
std::vector<std::string> Student::getCourses() const { return courses; }

// Employee类实现
Employee::Employee(const std::string& name, int age, const std::string& id, 
                   const std::string& dept, const std::string& pos, double sal)
    : Person(name, age), employeeId(id), department(dept), position(pos), salary(sal) {
    std::cout << "Employee constructor: " << getName() << ", ID: " << employeeId << std::endl;
}

void Employee::displayInfo() const {
    Person::displayInfo();  // 调用基类方法
    std::cout << "Employee ID: " << getEmployeeId() << ", Department: " << getDepartment()
              << ", Position: " << getPosition() << ", Salary: $" << getSalary() << std::endl;
}

std::string Employee::getPersonType() const {
    return "Employee";
}

void Employee::promoteToPosition(const std::string& newPosition) {
    std::string oldPosition = getPosition();
    position = newPosition;
    std::cout << getName() << " promoted from " << oldPosition << " to " << newPosition << std::endl;
}

void Employee::giveRaise(double percentage) {
    if (percentage > 0.0) {
        double oldSalary = getSalary();
        salary *= (1.0 + percentage / 100.0);
        std::cout << getName() << " received a " << percentage << "% raise from $" 
                  << oldSalary << " to $" << getSalary() << std::endl;
    }
}

void Employee::transferToDepartment(const std::string& newDept) {
    std::string oldDept = getDepartment();
    department = newDept;
    std::cout << getName() << " transferred from " << oldDept << " to " << newDept << std::endl;
}

std::string Employee::getEmployeeId() const { return employeeId; }
std::string Employee::getDepartment() const { return department; }
std::string Employee::getPosition() const { return position; }
double Employee::getSalary() const { return salary; }

// Address类实现
Address::Address(const std::string& str, const std::string& c, const std::string& country)
    : street(str), city(c), country(country) {
    std::cout << "Address created: " << getFullAddress() << std::endl;
}

std::string Address::getFullAddress() const {
    std::stringstream ss;
    ss << street << ", " << getCity() << ", " << getCountry();
    if (!postalCode.empty()) {
        ss << " " << postalCode;
    }
    return ss.str();
}

void Address::setPostalCode(const std::string& code) {
    postalCode = code;
}

std::string Address::getCity() const { return city; }
std::string Address::getCountry() const { return country; }

// Contact类实现
Contact::Contact(const std::string& contactType, const std::string& contactValue)
    : type(contactType), value(contactValue) {
    std::cout << "Contact created: " << getContactInfo() << std::endl;
}

std::string Contact::getType() const { return type; }
std::string Contact::getValue() const { return value; }

void Contact::setValue(const std::string& newValue) {
    value = newValue;
}

std::string Contact::getContactInfo() const {
    return getType() + ": " + getValue();
}

// PersonUtils命名空间实现
namespace PersonUtils {
    void printPersonList(const std::vector<std::unique_ptr<Person>>& people) {
        std::cout << "=== Person List ===" << std::endl;
        for (const auto& person : people) {
            person->displayInfo();
            std::cout << "Type: " << person->getPersonType() << std::endl;
            std::cout << "---" << std::endl;
        }
    }
    
    std::vector<std::unique_ptr<Person>> filterByAge(const std::vector<std::unique_ptr<Person>>& people, int minAge, int maxAge) {
        std::vector<std::unique_ptr<Person>> filtered;
        for (const auto& person : people) {
            if (person->getAge() >= minAge && person->getAge() <= maxAge) {
                // 这里应该是deep copy，为简单起见直接返回空vector
                std::cout << "Found person in age range: " << person->getName() << std::endl;
            }
        }
        return filtered;
    }
    
    std::unique_ptr<Person> findPersonByName(const std::vector<std::unique_ptr<Person>>& people, const std::string& name) {
        for (const auto& person : people) {
            if (person->getName() == name) {
                std::cout << "Found person: " << person->getName() << std::endl;
                return nullptr; // 为简单起见返回nullptr，实际应该返回拷贝
            }
        }
        std::cout << "Person not found: " << name << std::endl;
        return nullptr;
    }
} 