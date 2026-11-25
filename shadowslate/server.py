"""Connect the tabletop shadow desk to the photo preparation workspace."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, object_value

from . import exports, reading
from .store import Cards


def download(data, mime, filename):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename, as_attachment=True)


def create_app(directory=None):
    root = Path(directory or os.environ.get("SHADOWSLATE_DATA", ".shadowslate-data")).resolve()
    desk = preparation_app(root / "prepare")
    cards = Cards(root / "cards")
    api = Blueprint("shadowslate", __name__, url_prefix="/shadowslate")

    @desk.get("/")
    def home():
        return render_template("shadowslate.html")

    @api.get("/health")
    def health():
        return jsonify(status="ok", app="shadowslate")

    @api.get("/cards")
    def listing():
        return jsonify(cards=cards.list())

    @api.post("/cards")
    def create():
        request.max_content_length = 20 * 1024 * 1024
        upload = request.files.get("photo")
        if not upload or not upload.filename:
            raise Invalid("请选择投影原照")
        size, image = reading.decode(upload.stream.read(reading.MAX_IMAGE_BYTES + 1))
        return jsonify(cards.create(request.form.get("name", ""), Path(upload.filename).name[:140],
                                    size, image, reading.defaults(size))), 201

    @api.post("/practice")
    def practice():
        return jsonify(cards.demo()), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        return jsonify(cards.update(key, object_value(request.get_json(silent=True), "投影卡设置"))
                       if request.method == "PATCH" else cards.read(key))

    @api.get("/cards/<key>/capture.png")
    def capture(key):
        return send_file(cards.photo(key), mimetype="image/png", conditional=True)

    def measurement(key):
        card = cards.read(key, request.args.get("revision"))
        image = reading.pixels(cards.photo(key))
        return card, image, reading.report(image, card["settings"])

    @api.get("/cards/<key>/measurement")
    def measurement_json(key):
        card, _, result = measurement(key)
        return jsonify(revision=card["revision"], **result)

    @api.get("/cards/<key>/probe")
    def probe(key):
        card = cards.read(key, request.args.get("revision"))
        x = integer(request.args.get("x"), "X", 0, card["width"] - 1)
        y = integer(request.args.get("y"), "Y", 0, card["height"] - 1)
        image = reading.pixels(cards.photo(key))
        return jsonify(reading.point(image, x, y, card["settings"], reading.transition(image, card["settings"])))

    @api.get("/cards/<key>/edge")
    def edge(key):
        card = cards.read(key, request.args.get("revision"))
        x = integer(request.args.get("x"), "影边 X", 0, card["width"] - 1)
        y = integer(request.args.get("y"), "影边 Y", 0, card["height"] - 1)
        return jsonify(reading.transition(reading.pixels(cards.photo(key)),
                                          {**card["settings"], "edge": {"x": x, "y": y}}))

    @api.get("/cards/<key>/export/profile.csv")
    def profile(key):
        _, _, result = measurement(key)
        return download(exports.trace_table(result), "text/csv", "shadow-profile.csv")

    @api.get("/cards/<key>/export/proof.png")
    def proof(key):
        card, image, result = measurement(key)
        return download(exports.proof(image, card, result), "image/png", "shadow-proof.png")

    @api.get("/cards/<key>/export/observation.zip")
    def bundle(key):
        card, image, result = measurement(key)
        package = exports.package(card, cards.photo(key).read_bytes(), image, result)
        return download(package, "application/zip", f"shadowslate-r{card['revision']}.zip")

    @api.post("/cards/<key>/prepare")
    def prepare(key):
        card = cards.read(key, request.args.get("revision"))
        image = reading.pixels(cards.photo(key)).astype("float32") / 255
        services = desk.extensions
        asset = services["assets"].put(image, card["photo_name"])
        project = services["store"].create(f'{card["name"]} · 原片准备')
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return jsonify(url=url_for("index", project=project["id"])), 201

    desk.register_blueprint(api)
    return desk
