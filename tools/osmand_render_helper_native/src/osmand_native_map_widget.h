#pragma once

#include <OsmAndCore/QtExtensions.h>

#include <memory>

#include <QByteArray>
#include <QElapsedTimer>
#include <QOpenGLWidget>
#include <QPointF>
#include <QShowEvent>
#include <QString>
#include <QStringList>
#include <QTimer>

namespace OsmAnd
{
    class IMapRenderer;
    class ObfsCollection;
    class MapStylesCollection;
    class MapPresentationEnvironment;
    class MapPrimitiviser;
    class ObfMapObjectsProvider;
    class MapPrimitivesProvider;
    class MapObjectsSymbolsProvider;
    class MapRasterLayerProvider_Software;
}

class OsmAndNativeMapWidget final : public QOpenGLWidget
{
public:
    struct Configuration
    {
        QStringList obfPaths;
        QString resourcesRoot;
        QString stylePath;
        bool nightMode = false;
        double displayScale = 1.0;
    };

    static OsmAndNativeMapWidget* create(const Configuration& configuration, QWidget* parent, QString& errorMessage);

    ~OsmAndNativeMapWidget() override;

    double zoomLevel() const;
    double minZoomLevel() const;
    double maxZoomLevel() const;
    void setZoomLevel(double zoomLevel);
    void resetView();
    void panByPixels(double deltaX, double deltaY);
    void setCenterLonLat(double longitude, double latitude);
    QPointF centerLonLat() const;
    bool projectLonLat(double longitude, double latitude, QPointF& outScreenPoint) const;
    bool grabFrameRgba(QByteArray& pixelData, int& width, int& height, int& stride,
        bool& rendererIdle, bool& frameInvalidated, QString& errorMessage);

    bool isFrameReady(bool& rendererIdle, bool& frameInvalidated, QString& errorMessage);
    qint64 openGlInitializationMs() const;
    qint64 firstFrameMs() const;

protected:
    void initializeGL() override;
    void resizeGL(int width, int height) override;
    void paintGL() override;
    void showEvent(QShowEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void wheelEvent(QWheelEvent* event) override;

private:
    explicit OsmAndNativeMapWidget(const Configuration& configuration, QWidget* parent = nullptr);

    bool initializeResources(QString& errorMessage);
    bool ensureRenderer();
    void maybeDumpCaptionDiagnostics();
    void syncRendererViewport(bool forcedUpdate = false);
    void syncRendererCamera(bool forcedUpdate = false);
    void beginInteractiveRendering();
    void scheduleInteractiveRenderingFinish();
    void finishInteractiveRendering();
    void cleanupRenderer();
    void requestFullUpdate();
    void wrapCenter();
    double worldSize() const;

    static QPointF lonLatToNormalized(double longitude, double latitude);
    static QPointF normalizedToLonLat(double normalizedX, double normalizedY);

    Configuration _configuration;
    QString _styleName;
    QString _locale;
    QString _initError;
    bool _resourcesReady = false;
    QElapsedTimer _openGlInitializationTimer;
    qint64 _openGlInitializationMs = -1;
    qint64 _firstFrameMs = -1;
    bool _dragging = false;
    QPointF _lastMousePosition;
    double _centerX = 0.5;
    double _centerY = 0.5;
    double _zoomLevel = 2.0;
    double _minZoomLevel = 2.0;
    double _maxZoomLevel = 19.0;
    double _defaultZoomLevel = 2.0;
    QTimer _interactionTimer;
    bool _interactiveRendering = false;
    bool _symbolsSuspendedByInteraction = false;
    bool _captionDiagnosticsDumped = false;

    std::shared_ptr<OsmAnd::MapStylesCollection> _stylesCollection;
    std::shared_ptr<OsmAnd::ObfsCollection> _obfsCollection;
    std::shared_ptr<OsmAnd::MapPresentationEnvironment> _mapPresentationEnvironment;
    std::shared_ptr<OsmAnd::MapPrimitiviser> _primitiviser;
    std::shared_ptr<OsmAnd::ObfMapObjectsProvider> _mapObjectsProvider;
    std::shared_ptr<OsmAnd::MapPrimitivesProvider> _mapPrimitivesProvider;
    std::shared_ptr<OsmAnd::MapObjectsSymbolsProvider> _mapSymbolsProvider;
    std::shared_ptr<OsmAnd::MapRasterLayerProvider_Software> _mapRasterLayerProvider;
    std::shared_ptr<OsmAnd::IMapRenderer> _mapRenderer;
};
