#ifndef PERSON_H
#define PERSON_H

#include <string>
#include <vector>
#include <memory>

// 前向声明
class Address;
class Contact;

// 基础Person类
class Person {
protected:
    std::string name;
    int age;
    std::unique_ptr<Address> address;
    std::vector<std::unique_ptr<Contact>> contacts;
    
public:
    // 构造函数和析构函数
    Person();
    Person(const std::string& personName, int personAge);
    Person(const Person& other);  // 拷贝构造函数
    Person& operator=(const Person& other);  // 拷贝赋值操作符
    virtual ~Person();
    
    // 基本访问器
    std::string getName() const;
    int getAge() const;
    void setName(const std::string& newName);
    void setAge(int newAge);
    
    // 虚方法
    virtual void displayInfo() const;
    virtual std::string getPersonType() const;
    
    // 地址管理
    void setAddress(const std::string& street, const std::string& city, const std::string& country);
    std::string getAddressInfo() const;
    
    // 联系方式管理
    void addContact(const std::string& type, const std::string& value);
    void removeContact(const std::string& type);
    std::vector<std::string> getContactInfo() const;
    
    // 操作符重载
    bool operator==(const Person& other) const;
    bool operator<(const Person& other) const;
    
    // 静态方法
    static bool isValidAge(int age);
    static bool isValidName(const std::string& name);
};

// 学生类（继承自Person）
class Student : public Person {
private:
    std::string studentId;
    std::string major;
    double gpa;
    std::vector<std::string> courses;
    
public:
    Student(const std::string& name, int age, const std::string& id, const std::string& majorField);
    
    // 重写虚方法
    void displayInfo() const override;
    std::string getPersonType() const override;
    
    // 学生特有方法
    void setMajor(const std::string& newMajor);
    void setGPA(double newGpa);
    void addCourse(const std::string& courseName);
    void removeCourse(const std::string& courseName);
    
    // 访问器
    std::string getStudentId() const;
    std::string getMajor() const;
    double getGPA() const;
    std::vector<std::string> getCourses() const;
};

// 员工类（继承自Person）
class Employee : public Person {
private:
    std::string employeeId;
    std::string department;
    double salary;
    std::string position;
    
public:
    Employee(const std::string& name, int age, const std::string& id, 
             const std::string& dept, const std::string& pos, double sal);
    
    // 重写虚方法
    void displayInfo() const override;
    std::string getPersonType() const override;
    
    // 员工特有方法
    void promoteToPosition(const std::string& newPosition);
    void giveRaise(double percentage);
    void transferToDepartment(const std::string& newDept);
    
    // 访问器
    std::string getEmployeeId() const;
    std::string getDepartment() const;
    std::string getPosition() const;
    double getSalary() const;
};

// 地址类
class Address {
private:
    std::string street;
    std::string city;
    std::string country;
    std::string postalCode;
    
public:
    Address(const std::string& str, const std::string& c, const std::string& country);
    
    std::string getFullAddress() const;
    void setPostalCode(const std::string& code);
    std::string getCity() const;
    std::string getCountry() const;
};

// 联系方式类
class Contact {
private:
    std::string type;  // "email", "phone", "social"
    std::string value;
    
public:
    Contact(const std::string& contactType, const std::string& contactValue);
    
    std::string getType() const;
    std::string getValue() const;
    void setValue(const std::string& newValue);
    std::string getContactInfo() const;
};

// 工具命名空间
namespace PersonUtils {
    void printPersonList(const std::vector<std::unique_ptr<Person>>& people);
    std::vector<std::unique_ptr<Person>> filterByAge(const std::vector<std::unique_ptr<Person>>& people, int minAge, int maxAge);
    std::unique_ptr<Person> findPersonByName(const std::vector<std::unique_ptr<Person>>& people, const std::string& name);
}

#endif // PERSON_H 