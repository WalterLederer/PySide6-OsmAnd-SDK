#include "osmand_native_map_widget.h"

#include <QApplication>
#include <QImage>
#include <QTimer>

#include <cstdlib>
#include <iostream>

int main(int argc, char* argv[])
{
    if (argc < 5 || argc > 8)
    {
        std::cerr
            << "Usage: osmand_atlas_frame_poc.exe <map.obf> <resources-root> <style-file> <output.png> "
            << "[longitude] [latitude] [zoom]\n";
        return 2;
    }

    QApplication app(argc, argv);
    const auto arguments = app.arguments();

    const auto longitude = arguments.size() >= 6 ? arguments.at(5).toDouble() : 16.3738;
    const auto latitude = arguments.size() >= 7 ? arguments.at(6).toDouble() : 48.2082;
    const auto zoom = arguments.size() >= 8 ? arguments.at(7).toDouble() : 13.0;

    const OsmAndNativeMapWidget::Configuration configuration{
        arguments.at(1),
        arguments.at(2),
        arguments.at(3),
        false
    };

    QString errorMessage;
    auto* widget = OsmAndNativeMapWidget::create(configuration, nullptr, errorMessage);
    if (!widget)
    {
        std::cerr << "Widget creation failed: " << errorMessage.toStdString() << "\n";
        return 3;
    }

    widget->resize(1280, 960);
    widget->setCenterLonLat(longitude, latitude);
    widget->setZoomLevel(zoom);
    widget->setWindowTitle(QStringLiteral("OsmAnd Atlas RGBA frame POC"));
    widget->show();

    QObject::connect(&app, &QCoreApplication::aboutToQuit, widget, &QObject::deleteLater);

    // Let Atlas load vector data and asynchronously prepare symbols before capture.
    QTimer::singleShot(6000, &app, [widget, outputPath = arguments.at(4), &app]() {
        QByteArray rgba;
        int width = 0;
        int height = 0;
        int stride = 0;
        bool rendererIdle = false;
        bool frameInvalidated = true;
        QString captureError;

        if (!widget->grabFrameRgba(
                rgba, width, height, stride, rendererIdle, frameInvalidated, captureError))
        {
            std::cerr << "RGBA capture failed: " << captureError.toStdString() << "\n";
            app.exit(4);
            return;
        }

        const QImage image(
            reinterpret_cast<const uchar*>(rgba.constData()),
            width,
            height,
            stride,
            QImage::Format_RGBA8888);

        if (!image.copy().save(outputPath, "PNG"))
        {
            std::cerr << "Failed to save PNG: " << outputPath.toStdString() << "\n";
            app.exit(5);
            return;
        }

        std::cout
            << "PNG: " << outputPath.toStdString() << "\n"
            << "Size: " << width << " x " << height << "\n"
            << "Stride: " << stride << "\n"
            << "RGBA bytes: " << rgba.size() << "\n"
            << "Atlas idle: " << (rendererIdle ? "yes" : "no") << "\n"
            << "Frame invalidated: " << (frameInvalidated ? "yes" : "no") << "\n";

        app.exit(0);
    });

    return app.exec();
}
