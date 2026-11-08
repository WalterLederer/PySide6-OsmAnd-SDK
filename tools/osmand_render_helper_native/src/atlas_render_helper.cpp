#include "osmand_native_map_widget.h"

#include <QApplication>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMetaObject>
#include <QTimer>

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <limits>
#include <string>
#include <thread>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <io.h>
#endif

namespace
{
constexpr int kFramePollIntervalMs = 100;
constexpr int kFrameTimeoutMs = 30000;
constexpr int kMaximumDimension = 8192;
constexpr std::uint16_t kProtocolVersion = 1;
constexpr std::uint16_t kHeaderSize = 40;
constexpr std::uint32_t kPixelFormatRgba8 = 1;

#ifdef _WIN32
HANDLE g_protocolOutputHandle = INVALID_HANDLE_VALUE;

bool initializeOutputChannels()
{
    std::fflush(stdout);
    std::cout.flush();

    const HANDLE originalOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    const HANDLE errorOutput = GetStdHandle(STD_ERROR_HANDLE);
    if (originalOutput == nullptr || originalOutput == INVALID_HANDLE_VALUE
        || errorOutput == nullptr || errorOutput == INVALID_HANDLE_VALUE)
        return false;

    if (!DuplicateHandle(
            GetCurrentProcess(), originalOutput,
            GetCurrentProcess(), &g_protocolOutputHandle,
            0, FALSE, DUPLICATE_SAME_ACCESS))
        return false;

    if (!SetStdHandle(STD_OUTPUT_HANDLE, errorOutput)
        || _dup2(_fileno(stderr), _fileno(stdout)) != 0)
    {
        CloseHandle(g_protocolOutputHandle);
        g_protocolOutputHandle = INVALID_HANDLE_VALUE;
        return false;
    }

    std::cout.rdbuf(std::cerr.rdbuf());
    return true;
}

bool writeProtocolBytes(const void* data, std::size_t length)
{
    const auto* bytes = static_cast<const unsigned char*>(data);
    while (length > 0)
    {
        const DWORD chunk = static_cast<DWORD>(
            length > static_cast<std::size_t>(std::numeric_limits<DWORD>::max())
                ? std::numeric_limits<DWORD>::max() : length);
        DWORD written = 0;
        if (!WriteFile(g_protocolOutputHandle, bytes, chunk, &written, nullptr) || written == 0)
            return false;
        bytes += written;
        length -= written;
    }
    return true;
}
#else
bool initializeOutputChannels() { return true; }
bool writeProtocolBytes(const void* data, std::size_t length)
{
    return std::fwrite(data, 1, length, stdout) == length;
}
#endif

void writeU16(std::array<unsigned char, kHeaderSize>& header, std::size_t offset, std::uint16_t value)
{
    header[offset] = static_cast<unsigned char>(value & 0xff);
    header[offset + 1] = static_cast<unsigned char>((value >> 8) & 0xff);
}

void writeU32(std::array<unsigned char, kHeaderSize>& header, std::size_t offset, std::uint32_t value)
{
    for (int i = 0; i < 4; ++i)
        header[offset + static_cast<std::size_t>(i)] = static_cast<unsigned char>((value >> (i * 8)) & 0xff);
}

void writeU64(std::array<unsigned char, kHeaderSize>& header, std::size_t offset, std::uint64_t value)
{
    for (int i = 0; i < 8; ++i)
        header[offset + static_cast<std::size_t>(i)] = static_cast<unsigned char>((value >> (i * 8)) & 0xff);
}

void writeFrameHeader(
    std::uint32_t status,
    std::uint32_t requestId,
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t stride,
    std::uint32_t pixelFormat,
    std::uint64_t payloadLength)
{
    std::array<unsigned char, kHeaderSize> header{};
    header[0] = 'O';
    header[1] = 'S';
    header[2] = 'M';
    header[3] = 'A';
    writeU16(header, 4, kProtocolVersion);
    writeU16(header, 6, kHeaderSize);
    writeU32(header, 8, status);
    writeU32(header, 12, requestId);
    writeU32(header, 16, width);
    writeU32(header, 20, height);
    writeU32(header, 24, stride);
    writeU32(header, 28, pixelFormat);
    writeU64(header, 32, payloadLength);

    writeProtocolBytes(header.data(), header.size());
}

void writeError(std::uint32_t requestId, const QString& message)
{
    std::cerr << "[atlas-helper] request " << requestId << ": "
              << message.toStdString() << std::endl;
    writeFrameHeader(1, requestId, 0, 0, 0, 0, 0);
}

void writeRgbaFrame(
    std::uint32_t requestId,
    const QByteArray& rgba,
    int width,
    int height,
    int stride)
{
    writeFrameHeader(
        0,
        requestId,
        static_cast<std::uint32_t>(width),
        static_cast<std::uint32_t>(height),
        static_cast<std::uint32_t>(stride),
        kPixelFormatRgba8,
        static_cast<std::uint64_t>(rgba.size()));

    if (!rgba.isEmpty())
        writeProtocolBytes(rgba.constData(), static_cast<std::size_t>(rgba.size()));
}
}

int main(int argc, char* argv[])
{
    if (!initializeOutputChannels())
    {
        std::cerr << "[atlas-helper] failed to separate protocol output from log output" << std::endl;
        return 4;
    }

    if (argc != 5)
    {
        std::cerr
            << "Usage: osmand_atlas_render_helper.exe <map.obf> <resources-root> <style-file> <display-scale>\n"
            << "stdin: one JSON render command per line\n"
            << "stdout: OSMA v1 header (40 bytes, little-endian) followed by RGBA8 bytes\n";
        return 2;
    }

    bool displayScaleOk = false;
    const auto displayScale = QString::fromLocal8Bit(argv[4]).toDouble(&displayScaleOk);
    if (!displayScaleOk || !std::isfinite(displayScale) || displayScale < 1.0 || displayScale > 4.0)
    {
        std::cerr << "[atlas-helper] display-scale must be a finite number between 1.0 and 4.0" << std::endl;
        return 2;
    }

    qputenv("QT_ENABLE_HIGHDPI_SCALING", "0");

    QApplication app(argc, argv);
    app.setQuitOnLastWindowClosed(false);

    const OsmAndNativeMapWidget::Configuration configuration{
        QString::fromLocal8Bit(argv[1]),
        QString::fromLocal8Bit(argv[2]),
        QString::fromLocal8Bit(argv[3]),
        false,
        displayScale
    };

    QElapsedTimer widgetCreationTimer;
    widgetCreationTimer.start();
    QString errorMessage;
    auto* widget = OsmAndNativeMapWidget::create(configuration, nullptr, errorMessage);
    if (!widget)
    {
        std::cerr << "[atlas-helper] initialization failed: "
                  << errorMessage.toStdString() << std::endl;
        return 3;
    }

    std::cerr << "ATLAS_INIT display_scale=" << displayScale << std::endl;
    std::cerr << "ATLAS_INIT widget_create_ms=" << widgetCreationTimer.elapsed() << std::endl;

    widget->setAttribute(Qt::WA_DontShowOnScreen, true); 
    widget->setMinimumSize(1, 1);
    widget->resize(1280, 960);
    widget->setWindowTitle(QStringLiteral("OsmAnd Atlas Render Helper"));
    widget->move(100, 100);
    widget->setZoomLevel(13.0);
    widget->setCenterLonLat(16.3738, 48.2082);
    widget->show();

    QTimer captureTimer;
    captureTimer.setInterval(kFramePollIntervalMs);

    bool requestActive = false;
    std::uint32_t activeRequestId = 0;
    QElapsedTimer requestTimer;
    bool openGlTimingReported = false;
    bool firstFrameTimingReported = false;
    bool initializationReported = false;

    QObject::connect(&captureTimer, &QTimer::timeout, &app,
        [&]() {
            if (!openGlTimingReported && widget->openGlInitializationMs() >= 0)
            {
                std::cerr << "ATLAS_INIT opengl_initialization_ms="
                          << widget->openGlInitializationMs() << std::endl;
                openGlTimingReported = true;
            }
            if (!firstFrameTimingReported && widget->firstFrameMs() >= 0)
            {
                std::cerr << "ATLAS_INIT first_frame_after_gl_init_ms="
                          << widget->firstFrameMs() << std::endl;
                firstFrameTimingReported = true;
            }
            if (!requestActive)
            {
                captureTimer.stop();
                return;
            }

            bool rendererIdle = false;
            bool frameInvalidated = true;
            QString captureError;

            const auto stateChecked = widget->isFrameReady(
                rendererIdle, frameInvalidated, captureError);

            if (stateChecked && !initializationReported)
            {
                std::cerr << "ATLAS_INIT renderer_initialized=1" << std::endl;
                initializationReported = true;
            }

            if (stateChecked && rendererIdle && !frameInvalidated)
            {
                QByteArray rgba;
                int width = 0;
                int height = 0;
                int stride = 0;

                const auto captured = widget->grabFrameRgba(
                    rgba, width, height, stride,
                    rendererIdle, frameInvalidated, captureError);

                if (captured && rendererIdle && !frameInvalidated)
                {
                    captureTimer.stop();
                    requestActive = false;
                    writeRgbaFrame(activeRequestId, rgba, width, height, stride);
                    return;
                }
            }

            if (requestTimer.elapsed() >= kFrameTimeoutMs)
            {
                captureTimer.stop();
                requestActive = false;
                writeError(
                    activeRequestId,
                    captureError.isEmpty()
                    ? QStringLiteral("Atlas did not settle within %1 ms").arg(kFrameTimeoutMs)
                    : QStringLiteral("Frame readiness/capture failed: %1").arg(captureError));
            }
        });

    std::thread inputThread([&app, widget, displayScale, &captureTimer, &requestActive, &activeRequestId, &requestTimer]() {
        std::string line;
        while (std::getline(std::cin, line))
        {
            const auto commandLine = QString::fromUtf8(line.data(), static_cast<qsizetype>(line.size()));
            const auto parsedLine = QJsonDocument::fromJson(commandLine.toUtf8());
            const bool quitRequested = parsedLine.isObject()
                && parsedLine.object().value(QStringLiteral("type")).toString() == QStringLiteral("quit");
            QMetaObject::invokeMethod(&app, [&, commandLine]() {
                QJsonParseError parseError;
                const auto document = QJsonDocument::fromJson(commandLine.toUtf8(), &parseError);
                if (parseError.error != QJsonParseError::NoError || !document.isObject())
                {
                    writeError(0, QStringLiteral("Invalid JSON command: %1").arg(parseError.errorString()));
                    return;
                }

                const auto command = document.object();
                const auto type = command.value(QStringLiteral("type")).toString();
                if (type == QStringLiteral("quit"))
                {
                    app.quit();
                    return;
                }
                if (type != QStringLiteral("render"))
                {
                    writeError(0, QStringLiteral("Unsupported command type: %1").arg(type));
                    return;
                }

                const auto requestValue = command.value(QStringLiteral("requestId"));
                const auto widthValue = command.value(QStringLiteral("width"));
                const auto heightValue = command.value(QStringLiteral("height"));
                const auto longitudeValue = command.value(QStringLiteral("longitude"));
                const auto latitudeValue = command.value(QStringLiteral("latitude"));
                const auto zoomValue = command.value(QStringLiteral("zoom"));

                if (!requestValue.isDouble() || !widthValue.isDouble() || !heightValue.isDouble()
                    || !longitudeValue.isDouble() || !latitudeValue.isDouble() || !zoomValue.isDouble())
                {
                    writeError(0, QStringLiteral("render requires numeric requestId, width, height, longitude, latitude and zoom"));
                    return;
                }

                const auto requestNumber = requestValue.toDouble();
                const auto requestedWidth = widthValue.toInt(-1);
                const auto requestedHeight = heightValue.toInt(-1);
                const auto longitude = longitudeValue.toDouble();
                const auto latitude = latitudeValue.toDouble();
                const auto zoom = zoomValue.toDouble();

                if (requestNumber < 0.0 || requestNumber > std::numeric_limits<std::uint32_t>::max()
                    || requestedWidth < 1 || requestedWidth > kMaximumDimension
                    || requestedHeight < 1 || requestedHeight > kMaximumDimension
                    || longitude < -180.0 || longitude > 180.0
                    || latitude < -90.0 || latitude > 90.0
                    || zoom < 2.0 || zoom > 22.0)
                {
                    writeError(
                        requestNumber >= 0.0 && requestNumber <= std::numeric_limits<std::uint32_t>::max()
                            ? static_cast<std::uint32_t>(requestNumber) : 0,
                        QStringLiteral("render parameters are outside supported ranges"));
                    return;
                }

                const auto requestId = static_cast<std::uint32_t>(requestNumber);
                if (requestActive)
                {
                    writeError(requestId, QStringLiteral("A render request is already active; send one command at a time"));
                    return;
                }

                requestActive = true;
                activeRequestId = requestId;
                requestTimer.restart();

                widget->resize(requestedWidth, requestedHeight);
                // The XYZ tile always represents the same geographic extent.
                // A 512px framebuffer has twice as many pixels per axis as the
                // 256px baseline, so increase the native map zoom by one level
                // to compensate for the doubled viewport dimensions. Without
                // this, OsmAnd renders a wider geographic area into the same
                // MapLibre tile footprint and neighbouring tiles do not align.
                const auto pixelScaleX = static_cast<double>(requestedWidth) / 256.0;
                const auto pixelScaleY = static_cast<double>(requestedHeight) / 256.0;
                const auto effectivePixelScaleX = pixelScaleX * displayScale;
                const auto effectivePixelScaleY = pixelScaleY * displayScale;
                const auto zoomCompensationX = std::log2(effectivePixelScaleX);
                const auto zoomCompensationY = std::log2(effectivePixelScaleY);

                // Keep the existing X-based zoom compensation unchanged while
                // collecting geometry diagnostics for both axes.
                const auto zoomCompensation = zoomCompensationX;
                const auto compensatedZoom = zoom + zoomCompensation;

                if (std::getenv("OSMAND_ATLAS_DEBUG_GEOMETRY") != nullptr)
                {
                    std::cerr
                        << "[OsmAnd Atlas][geometry] request=" << requestId
                        << " requested=" << requestedWidth << "x" << requestedHeight
                        << " displayScale=" << displayScale
                        << " pixelScaleXY=" << pixelScaleX << "x" << pixelScaleY
                        << " effectiveScaleXY=" << effectivePixelScaleX << "x" << effectivePixelScaleY
                        << " zoom=" << zoom
                        << " compensationXY=" << zoomCompensationX << "x" << zoomCompensationY
                        << " compensatedZoom=" << compensatedZoom
                        << " centerLonLat=" << longitude << "," << latitude
                        << std::endl;
                }

                widget->setZoomLevel(compensatedZoom);
                widget->setCenterLonLat(longitude, latitude);
                widget->update();
                captureTimer.start();
            }, Qt::QueuedConnection);
            if (quitRequested)
                break;
        }

        QMetaObject::invokeMethod(&app, [&app]() { app.quit(); }, Qt::QueuedConnection);
    });

    const auto exitCode = app.exec();
    if (inputThread.joinable())
        inputThread.join();

    delete widget;
#ifdef _WIN32
    if (g_protocolOutputHandle != INVALID_HANDLE_VALUE)
    {
        CloseHandle(g_protocolOutputHandle);
        g_protocolOutputHandle = INVALID_HANDLE_VALUE;
    }
#endif
    return exitCode;
}
