"""EPUB container parsing: table of contents, spine, and metadata."""

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote


class Epub:
    NS = {
        "DAISY": "http://www.daisy.org/z3986/2005/ncx/",
        "OPF": "http://www.idpf.org/2007/opf",
        "CONT": "urn:oasis:names:tc:opendocument:xmlns:container",
        "XHTML": "http://www.w3.org/1999/xhtml",
        "EPUB": "http://www.idpf.org/2007/ops"
    }

    def __init__(self, fileepub):
        self.path = os.path.abspath(fileepub)
        self.file = zipfile.ZipFile(fileepub, "r")
        cont = ET.parse(self.file.open("META-INF/container.xml"))
        self.rootfile = cont.find(
            "CONT:rootfiles/CONT:rootfile",
            self.NS
        ).attrib["full-path"]
        self.rootdir = os.path.dirname(self.rootfile)\
            + "/" if os.path.dirname(self.rootfile) != "" else ""
        cont = ET.parse(self.file.open(self.rootfile))
        # EPUB3
        self.version = cont.getroot().get("version")
        if self.version == "2.0":
            # self.toc = self.rootdir + cont.find("OPF:manifest/*[@id='ncx']", self.NS).get("href")
            self.toc = self.rootdir\
                + cont.find(
                    "OPF:manifest/*[@media-type='application/x-dtbncx+xml']",
                    self.NS
                ).get("href")
        elif self.version == "3.0":
            self.toc = self.rootdir\
                + cont.find(
                    "OPF:manifest/*[@properties='nav']",
                    self.NS
                ).get("href")

        self.contents = []
        self.toc_entries = []

    def get_meta(self):
        meta = []
        # why self.file.read(self.rootfile) problematic
        cont = ET.fromstring(self.file.open(self.rootfile).read())
        for i in cont.findall("OPF:metadata/*", self.NS):
            if i.text is not None:
                meta.append([re.sub("{.*?}", "", i.tag), i.text])
        return meta

    def initialize(self):
        cont = ET.parse(self.file.open(self.rootfile)).getroot()
        manifest = []
        for i in cont.findall("OPF:manifest/*", self.NS):
            # EPUB3
            # if i.get("id") != "ncx" and i.get("properties") != "nav":
            if i.get("media-type") != "application/x-dtbncx+xml"\
               and i.get("properties") != "nav":
                manifest.append([
                    i.get("id"),
                    i.get("href")
                ])

        spine, contents = [], []
        for i in cont.findall("OPF:spine/*", self.NS):
            spine.append(i.get("idref"))
        for i in spine:
            for j in manifest:
                if i == j[0]:
                    self.contents.append(self.rootdir+unquote(j[1]))
                    contents.append(unquote(j[1]))
                    # Don't remove from manifest to avoid iteration issues
                    break

        toc = ET.parse(self.file.open(self.toc)).getroot()
        # EPUB3
        if self.version == "2.0":
            navPoints = toc.findall("DAISY:navMap//DAISY:navPoint", self.NS)
        elif self.version == "3.0":
            navPoints = toc.findall(
                "XHTML:body//XHTML:nav[@EPUB:type='toc']//XHTML:a",
                self.NS
            )
        for i in contents:
            name = "-"
            for j in navPoints:
                # EPUB3
                if self.version == "2.0":
                    # if i == unquote(j.find("DAISY:content", self.NS).get("src")):
                    if re.search(i, unquote(j.find("DAISY:content", self.NS).get("src"))) is not None:
                        name = j.find("DAISY:navLabel/DAISY:text", self.NS).text
                        break
                elif self.version == "3.0":
                    # if i == unquote(j.get("href")):
                    if re.search(i, unquote(j.get("href"))) is not None:
                        name = "".join(list(j.itertext()))
                        break
            self.toc_entries.append(name)


def dots_path(curr, tofi):
    """Resolve tofi (a manifest-relative href, possibly with '../') against
    curr (the current chapter's path within the EPUB) into a zip-member path."""
    candir = curr.split("/")
    tofi = tofi.split("/")
    alld = tofi.count("..")
    t = len(candir)
    candir = candir[0:t-alld-1]
    try:
        while True:
            tofi.remove("..")
    except ValueError:
        pass
    return "/".join(candir+tofi)

