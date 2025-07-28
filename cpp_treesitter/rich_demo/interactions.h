#pragma once
#include <iostream>

namespace Interactions {

    class Worker {
    public:
        void do_work() {
            std::cout << "Worker is doing work." << std::endl;
        }
    };

    class Manager {
    public:
        void manage() {
            Worker w;
            std::cout << "Manager asks worker to do work." << std::endl;
            w.do_work(); // 明确的跨类调用
        }
    };

} 