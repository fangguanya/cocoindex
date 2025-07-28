#include "template_container.h"
#include <algorithm>
#include <iostream>

// Container<T> 模板类实现
template<typename T>
Container<T>::Container() {}

template<typename T>
Container<T>::~Container() {}

template<typename T>
void Container<T>::add(const T& item) {
    items.push_back(item);
}

template<typename T>
T Container<T>::get(size_t index) const {
    if (index >= items.size()) {
        throw std::out_of_range("Index out of range");
    }
    return items[index];
}

template<typename T>
size_t Container<T>::size() const {
    return items.size();
}

template<typename T>
bool Container<T>::empty() const {
    return items.empty();
}

template<typename T>
void Container<T>::clear() {
    items.clear();
}

template<typename T>
template<typename Predicate>
void Container<T>::removeIf(Predicate pred) {
    items.erase(std::remove_if(items.begin(), items.end(), pred), items.end());
}

template<typename T>
void Container<T>::print() const {
    std::cout << "Container contents: ";
    for (const auto& item : items) {
        std::cout << item << " ";
    }
    std::cout << std::endl;
}

// Pair<K,V> 模板类实现
template<typename K, typename V>
Pair<K, V>::Pair(const K& k, const V& v) : key(k), value(v) {}

template<typename K, typename V>
K Pair<K, V>::getKey() const {
    return key;
}

template<typename K, typename V>
V Pair<K, V>::getValue() const {
    return value;
}

template<typename K, typename V>
void Pair<K, V>::setKey(const K& k) {
    key = k;
}

template<typename K, typename V>
void Pair<K, V>::setValue(const V& v) {
    value = v;
}

template<typename K, typename V>
bool Pair<K, V>::operator==(const Pair<K, V>& other) const {
    return key == other.key && value == other.value;
}

template<typename K, typename V>
bool Pair<K, V>::operator!=(const Pair<K, V>& other) const {
    return !(*this == other);
}

// Stack<T> 模板类实现
template<typename T>
Stack<T>::Stack() : Container<T>() {}

template<typename T>
void Stack<T>::push(const T& item) {
    this->add(item);
}

template<typename T>
T Stack<T>::pop() {
    if (this->empty()) {
        throw std::runtime_error("Stack is empty");
    }
    T item = this->get(this->size() - 1);
    this->items.pop_back();
    return item;
}

template<typename T>
T Stack<T>::top() const {
    if (this->empty()) {
        throw std::runtime_error("Stack is empty");
    }
    return this->get(this->size() - 1);
}

template<typename T>
void Stack<T>::print() const {
    std::cout << "Stack contents (top to bottom): ";
    for (int i = this->size() - 1; i >= 0; --i) {
        std::cout << this->get(i) << " ";
    }
    std::cout << std::endl;
}

// Container<std::string> 特化实现
Container<std::string>::Container() {}

void Container<std::string>::add(const std::string& item) {
    items.push_back(item);
}

std::string Container<std::string>::get(size_t index) const {
    if (index >= items.size()) {
        throw std::out_of_range("Index out of range");
    }
    return items[index];
}

size_t Container<std::string>::size() const {
    return items.size();
}

void Container<std::string>::printUpperCase() const {
    std::cout << "String container (uppercase): ";
    for (const auto& item : items) {
        std::string upper = item;
        std::transform(upper.begin(), upper.end(), upper.begin(), ::toupper);
        std::cout << upper << " ";
    }
    std::cout << std::endl;
}

// 显式模板实例化（用于演示）
template class Container<int>;
template class Container<double>;
template class Pair<std::string, int>;
template class Pair<int, std::string>;
template class Stack<int>;
template class Stack<std::string>; 