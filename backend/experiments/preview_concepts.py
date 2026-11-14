import argparse
from pathlib import Path

from backend.config import Config
from backend.documents import SUPPORTED_EXTENSIONS, read_document
from backend.knowledge_graph.preview import TextSource, generate_graph_preview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--target-concepts", type=int,
        help="objetivo aproximado de nodos tras fusionar conceptos solapados",
    )
    parser.add_argument(
        "--with-prerequisites",
        action="store_true",
        help="propone dependencias entre los conceptos extraídos, sin guardarlas",
    )
    args = parser.parse_args()

    config = Config.from_env()
    if args.file.suffix.lower() not in SUPPORTED_EXTENSIONS:
        parser.error("se admiten archivos .md, .markdown y .txt")
    print("Generando propuesta del grafo...", flush=True)
    preview = generate_graph_preview(
        [TextSource(source_id="archivo-1", title=args.file.name, text=read_document(args.file))],
        config,
        target_concepts=args.target_concepts,
        include_prerequisites=args.with_prerequisites,
    )
    print(f"Fragmentos enviados: {sum(item.fragment_count for item in preview.inputs)}")
    labels = {node.ref: node.label for node in preview.nodes}

    if args.target_concepts is not None:
        original_count = len(preview.nodes) + sum(
            len(merge.from_labels) - 1 for merge in preview.merges
        )
        print(f"Consolidación: {original_count} → {len(preview.nodes)} nodos; "
              f"{len(preview.merges)} grupos fusionados")
        for merge in preview.merges:
            print(f"  {', '.join(merge.from_labels)} → {labels[merge.to_ref]}")

    print(f"\nConceptos encontrados: {len(preview.nodes)}")
    for number, node in enumerate(preview.nodes, 1):
        print(f"\n{number}. {node.label}")
        print(f"   {node.description}")
        for source in node.sources:
            section = " > ".join(source.section_path) or "sin sección"
            print(f"   Fuente: {source.title}, {section}, "
                  f"fragmento {source.chunk_index + 1} ({source.source_id})")

    if args.with_prerequisites:
        print(f"\nDependencias propuestas: {len(preview.edges)}")
        for number, edge in enumerate(preview.edges, 1):
            print(f"\n{number}. {labels[edge.source_ref]} → {labels[edge.target_ref]}")
            print(f"   Motivo propuesto: {edge.reason}")
        print("\nEstas relaciones son inferencias del modelo; no se han guardado ni aprobado.")


if __name__ == "__main__":
    main()
