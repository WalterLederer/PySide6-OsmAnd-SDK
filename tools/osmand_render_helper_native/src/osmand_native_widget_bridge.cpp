#include "osmand_native_map_widget.h"
#include "osmand_search_service.h"

#include <algorithm>
#include <cstring>
#include <cwchar>

#include <QString>
#include <QWidget>

// Cross-platform export macro
#if defined(_WIN32) || defined(__CYGWIN__)
    #define OSMAND_EXPORT __declspec(dllexport)
#else
    #define OSMAND_EXPORT __attribute__((visibility("default")))
#endif

namespace
{
void writeErrorMessage(const QString& message, wchar_t* buffer, int bufferCapacity)
{
    if (!buffer || bufferCapacity <= 0)
        return;

    const auto utf16 = message.toStdWString();
    const auto copyLength = std::min(static_cast<int>(utf16.size()), bufferCapacity - 1);
    if (copyLength > 0)
        std::wmemcpy(buffer, utf16.c_str(), copyLength);
    buffer[copyLength] = L'\0';
}

inline OsmAndNativeMapWidget* widgetFromPointer(void* widgetPointer)
{
    return reinterpret_cast<OsmAndNativeMapWidget*>(widgetPointer);
}

inline OsmAndSearchService* searchServiceFromPointer(void* servicePointer)
{
    return reinterpret_cast<OsmAndSearchService*>(servicePointer);
}
}

extern "C"
{
OSMAND_EXPORT void* osmand_create_map_widget(
    void* parentWidgetPointer,
    const wchar_t* obfPath,
    const wchar_t* resourcesRoot,
    const wchar_t* stylePath,
    int nightMode,
    wchar_t* errorBuffer,
    int errorBufferCapacity)
{
    const auto configuration = OsmAndNativeMapWidget::Configuration{
        QString::fromWCharArray(obfPath ? obfPath : L""),
        QString::fromWCharArray(resourcesRoot ? resourcesRoot : L""),
        QString::fromWCharArray(stylePath ? stylePath : L""),
        nightMode != 0,
    };

    QString errorMessage;
    auto* widget = OsmAndNativeMapWidget::create(
        configuration,
        reinterpret_cast<QWidget*>(parentWidgetPointer),
        errorMessage);
    if (!widget)
    {
        writeErrorMessage(errorMessage, errorBuffer, errorBufferCapacity);
        return nullptr;
    }

    return widget;
}

OSMAND_EXPORT double osmand_widget_get_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->zoomLevel();
    return 0.0;
}

OSMAND_EXPORT double osmand_widget_get_min_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->minZoomLevel();
    return 0.0;
}

OSMAND_EXPORT double osmand_widget_get_max_zoom(void* widgetPointer)
{
    if (const auto* widget = widgetFromPointer(widgetPointer))
        return widget->maxZoomLevel();
    return 0.0;
}

OSMAND_EXPORT void osmand_widget_set_zoom(void* widgetPointer, double zoomLevel)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->setZoomLevel(zoomLevel);
}

OSMAND_EXPORT void osmand_widget_reset_view(void* widgetPointer)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->resetView();
}

OSMAND_EXPORT void osmand_widget_pan_by_pixels(void* widgetPointer, double deltaX, double deltaY)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->panByPixels(deltaX, deltaY);
}

OSMAND_EXPORT void osmand_widget_set_center_lonlat(void* widgetPointer, double longitude, double latitude)
{
    if (auto* widget = widgetFromPointer(widgetPointer))
        widget->setCenterLonLat(longitude, latitude);
}

OSMAND_EXPORT void osmand_widget_get_center_lonlat(void* widgetPointer, double* longitude, double* latitude)
{
    if (!longitude || !latitude)
        return;

    if (const auto* widget = widgetFromPointer(widgetPointer))
    {
        const auto center = widget->centerLonLat();
        *longitude = center.x();
        *latitude = center.y();
        return;
    }

    *longitude = 0.0;
    *latitude = 0.0;
}

OSMAND_EXPORT int osmand_widget_project_lonlat(
    void* widgetPointer,
    double longitude,
    double latitude,
    double* screenX,
    double* screenY)
{
    if (!screenX || !screenY)
        return 0;

    if (const auto* widget = widgetFromPointer(widgetPointer))
    {
        QPointF screenPoint;
        if (widget->projectLonLat(longitude, latitude, screenPoint))
        {
            *screenX = screenPoint.x();
            *screenY = screenPoint.y();
            return 1;
        }
    }

    *screenX = 0.0;
    *screenY = 0.0;
    return 0;
}

OSMAND_EXPORT void* osmand_create_search_service(
    const wchar_t* obfPath,
    const wchar_t* resourcesRoot,
    wchar_t* errorBuffer,
    int errorBufferCapacity)
{
    auto service = std::make_unique<OsmAndSearchService>(OsmAndSearchService::Configuration{
        QString::fromWCharArray(obfPath ? obfPath : L""),
        QString::fromWCharArray(resourcesRoot ? resourcesRoot : L""),
    });

    QString errorMessage;
    if (!service->initialize(errorMessage))
    {
        writeErrorMessage(errorMessage, errorBuffer, errorBufferCapacity);
        return nullptr;
    }

    return service.release();
}

OSMAND_EXPORT void osmand_destroy_search_service(void* servicePointer)
{
    delete searchServiceFromPointer(servicePointer);
}

OSMAND_EXPORT void osmand_abort_search(void* servicePointer)
{
    if (auto* service = searchServiceFromPointer(servicePointer))
        service->abort();
}

OSMAND_EXPORT int osmand_search_query(
    void* servicePointer,
    const wchar_t* query,
    int limit,
    const wchar_t* locale,
    int includePoiFallback,
    wchar_t* outputBuffer,
    int outputBufferCapacity,
    wchar_t* errorBuffer,
    int errorBufferCapacity)
{
    if (!outputBuffer || outputBufferCapacity <= 0)
    {
        writeErrorMessage(QStringLiteral("output buffer is not available"), errorBuffer, errorBufferCapacity);
        return 0;
    }

    const auto* service = searchServiceFromPointer(servicePointer);
    if (!service)
    {
        writeErrorMessage(QStringLiteral("search service pointer is null"), errorBuffer, errorBufferCapacity);
        return 0;
    }

    QString errorMessage;
    const auto payload = service->search(
        QString::fromWCharArray(query ? query : L""),
        limit,
        QString::fromWCharArray(locale ? locale : L""),
        includePoiFallback != 0,
        errorMessage);
    if (!errorMessage.isEmpty())
    {
        writeErrorMessage(errorMessage, errorBuffer, errorBufferCapacity);
        return 0;
    }

    const auto payloadWStr = payload.toStdWString();
    if (static_cast<int>(payloadWStr.size()) >= outputBufferCapacity)
    {
        writeErrorMessage(
            QStringLiteral("search result payload exceeds output buffer capacity"),
            errorBuffer,
            errorBufferCapacity);
        return 0;
    }
    std::wmemcpy(outputBuffer, payloadWStr.c_str(), payloadWStr.size() + 1);
    return 1;
}
}
