#include "shared_memory.h"
#include <cstring>

namespace fthr {

    SharedMemory::SharedMemory() : file_mapping_(nullptr), layout_(nullptr) {}

    SharedMemory::~SharedMemory() { Shutdown(); }

    bool SharedMemory::Initialize(const wchar_t* name) {
        file_mapping_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr,
            PAGE_READWRITE, 0, sizeof(SharedMemoryLayout), name);

        if (!file_mapping_)
            file_mapping_ = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, name);

        if (!file_mapping_) return false;

        layout_ = (SharedMemoryLayout*)MapViewOfFile(file_mapping_,
            FILE_MAP_ALL_ACCESS, 0, 0, sizeof(SharedMemoryLayout));

        if (!layout_) {
            CloseHandle(file_mapping_);
            file_mapping_ = nullptr;
            return false;
        }

        if (!layout_->is_initialized) {
            memset(layout_, 0, sizeof(SharedMemoryLayout));
            layout_->is_initialized = true;
        }

        return true;
    }

    void SharedMemory::Shutdown() {
        if (layout_) {
            UnmapViewOfFile(layout_);
            layout_ = nullptr;
        }
        if (file_mapping_) {
            CloseHandle(file_mapping_);
            file_mapping_ = nullptr;
        }
    }

}