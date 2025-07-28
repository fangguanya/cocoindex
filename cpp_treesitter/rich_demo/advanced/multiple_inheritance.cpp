#include "multiple_inheritance.h"
#include <fstream>
#include <sstream>

// Identifiable类实现
Identifiable::Identifiable(const std::string& id, const std::string& name) 
    : id(id), name(name) {}

std::string Identifiable::getId() const {
    return id;
}

std::string Identifiable::getName() const {
    return name;
}

void Identifiable::setName(const std::string& newName) {
    name = newName;
}

// Document类实现
Document::Document(const std::string& id, const std::string& name, const std::string& content)
    : Identifiable(id, name), content(content), format("text") {}

std::string Document::getType() const {
    return "Document";
}

void Document::print() const {
    std::cout << "=== Document: " << getName() << " ===" << std::endl;
    std::cout << "ID: " << getId() << std::endl;
    std::cout << "Format: " << format << std::endl;
    std::cout << "Content: " << content << std::endl;
    std::cout << "========================" << std::endl;
}

std::string Document::getPrintFormat() const {
    return "Document[" + format + "]";
}

std::string Document::serialize() const {
    std::ostringstream oss;
    oss << "{\"id\":\"" << getId() << "\",";
    oss << "\"name\":\"" << getName() << "\",";
    oss << "\"type\":\"" << getType() << "\",";
    oss << "\"format\":\"" << format << "\",";
    oss << "\"content\":\"" << content << "\"}";
    return oss.str();
}

void Document::deserialize(const std::string& data) {
    // 简化的反序列化实现
    std::cout << "Deserializing document from: " << data.substr(0, 50) << "..." << std::endl;
}

std::string Document::getSerializationFormat() const {
    return "JSON";
}

std::string Document::getContent() const {
    return content;
}

void Document::setContent(const std::string& newContent) {
    content = newContent;
}

void Document::setFormat(const std::string& newFormat) {
    format = newFormat;
}

// MultimediaFile类实现
MultimediaFile::MultimediaFile(const std::string& id, const std::string& name, 
                               const std::string& content, const std::string& mediaType)
    : Document(id, name, content), fileSize(0), mediaType(mediaType) {}

std::string MultimediaFile::getType() const {
    return "MultimediaFile";
}

void MultimediaFile::print() const {
    std::cout << "=== Multimedia File: " << getName() << " ===" << std::endl;
    std::cout << "ID: " << getId() << std::endl;
    std::cout << "Media Type: " << mediaType << std::endl;
    std::cout << "File Size: " << fileSize << " bytes" << std::endl;
    std::cout << "Content: " << getContent() << std::endl;
    std::cout << "Metadata count: " << metadata.size() << std::endl;
    std::cout << "==============================" << std::endl;
}

std::string MultimediaFile::serialize() const {
    std::ostringstream oss;
    oss << "{\"id\":\"" << getId() << "\",";
    oss << "\"name\":\"" << getName() << "\",";
    oss << "\"type\":\"" << getType() << "\",";
    oss << "\"mediaType\":\"" << mediaType << "\",";
    oss << "\"fileSize\":" << fileSize << ",";
    oss << "\"content\":\"" << getContent() << "\",";
    oss << "\"metadataCount\":" << metadata.size() << "}";
    return oss.str();
}

bool MultimediaFile::save(const std::string& filename) const {
    std::ofstream file(filename);
    if (file.is_open()) {
        file << serialize();
        file.close();
        std::cout << "Saved multimedia file to: " << filename << std::endl;
        return true;
    }
    return false;
}

bool MultimediaFile::load(const std::string& filename) {
    std::ifstream file(filename);
    if (file.is_open()) {
        std::string content((std::istreambuf_iterator<char>(file)),
                           std::istreambuf_iterator<char>());
        file.close();
        deserialize(content);
        std::cout << "Loaded multimedia file from: " << filename << std::endl;
        return true;
    }
    return false;
}

size_t MultimediaFile::getStorageSize() const {
    return fileSize;
}

void MultimediaFile::addMetadata(const std::string& metadata) {
    this->metadata.push_back(metadata);
}

std::vector<std::string> MultimediaFile::getMetadata() const {
    return metadata;
}

std::string MultimediaFile::getMediaType() const {
    return mediaType;
}

void MultimediaFile::setFileSize(size_t size) {
    fileSize = size;
}

// Base类实现
Base::Base(int value) : baseValue(value) {}

void Base::showBase() const {
    std::cout << "Base value: " << baseValue << std::endl;
}

int Base::getBaseValue() const {
    return baseValue;
}

// Left类实现
Left::Left(int baseVal, int leftVal) : Base(baseVal), leftValue(leftVal) {}

void Left::showLeft() const {
    std::cout << "Left value: " << leftValue << std::endl;
}

int Left::getLeftValue() const {
    return leftValue;
}

// Right类实现
Right::Right(int baseVal, int rightVal) : Base(baseVal), rightValue(rightVal) {}

void Right::showRight() const {
    std::cout << "Right value: " << rightValue << std::endl;
}

int Right::getRightValue() const {
    return rightValue;
}

// Diamond类实现
Diamond::Diamond(int baseVal, int leftVal, int rightVal, int diamondVal)
    : Base(baseVal), Left(baseVal, leftVal), Right(baseVal, rightVal), diamondValue(diamondVal) {}

void Diamond::showBase() const {
    std::cout << "Diamond override - Base value: " << baseValue << std::endl;
}

void Diamond::showLeft() const {
    std::cout << "Diamond override - Left value: " << leftValue << std::endl;
}

void Diamond::showRight() const {
    std::cout << "Diamond override - Right value: " << rightValue << std::endl;
}

void Diamond::showDiamond() const {
    std::cout << "Diamond value: " << diamondValue << std::endl;
}

void Diamond::showAll() const {
    std::cout << "=== Diamond Object Values ===" << std::endl;
    showBase();
    showLeft();
    showRight();
    showDiamond();
    std::cout << "============================" << std::endl;
}

int Diamond::getDiamondValue() const {
    return diamondValue;
} 